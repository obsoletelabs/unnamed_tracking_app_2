"""Actual populated/adoption upgrades preserve existing user-owned notification rows."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings


def test_invitation_upgrade_adoption_and_downgrade_preserve_user_history():
    schema = "invitation_migration_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
    connection_url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    scoped = base.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)
    command = [sys.executable, "-m", "alembic", "-c", "alembic.ini"]

    def migrate(*arguments):
        subprocess.run(
            command + list(arguments),
            cwd=Path(__file__).parents[1],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

    with psycopg.connect(connection_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", "513bc3715722")
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            user, notice = uuid4(), uuid4()
            connection.execute(
                "INSERT INTO users (id,username,email,password_hash,is_active,is_admin,created_at,updated_at) VALUES (%s,%s,%s,'unused',true,false,1,1)",
                (user, str(user), f"{user}@example.test"),
            )
            connection.execute(
                "INSERT INTO notifications (id,user_id,kind,media_type,media_id,title,body,event_at,dedupe_key,event_type,source,required_trust,purpose,severity,inbox_visible,created_at) VALUES (%s,%s,'movie_released','movie',%s,'Existing','Content',1,'existing','media.movie.released','host',1,'standard','info',true,1)",
                (notice, user, uuid4()),
            )
            migrate("upgrade", "head")
            assert connection.execute(
                "SELECT user_id,pending_recipient_id,title FROM notifications WHERE id=%s",
                (notice,),
            ).fetchone() == (user, None, "Existing")
            assert connection.execute(
                "SELECT conname FROM pg_constraint WHERE connamespace=%s::regnamespace AND conname LIKE 'ck_%%recipient_owner' ORDER BY conname",
                (schema,),
            ).fetchall() == [
                ("ck_notification_destination_recipient_owner",),
                ("ck_notifications_recipient_owner",),
            ]
            # Existing partial/adopted current schema must tolerate the upgrade again.
            migrate("stamp", "513bc3715722")
            migrate("upgrade", "head")
            assert connection.execute(
                "SELECT count(*) FROM notifications WHERE id=%s", (notice,)
            ).fetchone() == (1,)
            migrate("downgrade", "513bc3715722")
            assert connection.execute(
                "SELECT user_id,title FROM notifications WHERE id=%s", (notice,)
            ).fetchone() == (user, "Existing")
            migrate("upgrade", "head")
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
