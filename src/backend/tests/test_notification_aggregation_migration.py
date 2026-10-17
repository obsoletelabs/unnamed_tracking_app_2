"""Populated upgrade keeps queued deliveries and adds empty aggregation state; downgrade is clean."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings

PREVIOUS_HEAD = "9d4b2e6f1a35"


def test_aggregation_migration_preserves_queued_deliveries():
    schema = f"notification_aggregation_migration_{uuid4().hex}"
    base = make_url(settings.DATABASE_URL)
    environment = {
        **os.environ,
        "DATABASE_URL": base.update_query_dict(
            {"options": f"-csearch_path={schema}"}
        ).render_as_string(hide_password=False),
    }
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)
    directory = Path(__file__).resolve().parents[1]

    def migrate(*arguments, successful=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert (result.returncode == 0) is successful, result.stderr
        return result

    url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", PREVIOUS_HEAD)
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            owner, notice, destination, delivery = uuid4(), uuid4(), uuid4(), uuid4()
            connection.execute(
                """INSERT INTO users (id, username, email, password_hash,
                   is_active, is_admin, created_at, updated_at)
                   VALUES (%s, %s, %s, 'unused', true, false, 1, 1)""",
                (owner, str(owner), f"{owner}@example.test"),
            )
            connection.execute(
                """INSERT INTO notifications (id, user_id, kind, media_type, media_id,
                   title, body, event_at, dedupe_key, created_at, event_type, source,
                   required_trust, purpose, severity, inbox_visible)
                   VALUES (%s, %s, 'episode_aired', 'tv', %s, 'T', 'B', 1, 'k', 1,
                   'media.episode.aired', 'host', 1, 'standard', 'info', true)""",
                (notice, owner, uuid4()),
            )
            connection.execute(
                """INSERT INTO notification_destinations (id, user_id, provider_id,
                   endpoint_key, kind, channel_context, privacy, revision, enabled, active,
                   recovery_allowed, created_at)
                   VALUES (%s, %s, 'core.smtp', 'e', 'email', 'external', 1, 1, true, true,
                   false, 1)""",
                (destination, owner),
            )
            connection.execute(
                """INSERT INTO notification_deliveries (id, notification_id, provider_id,
                   destination_id, destination_revision, projection, status, attempts,
                   next_attempt_at) VALUES (%s, %s, 'core.smtp', %s, 1, 'canonical',
                   'pending', 0, 5)""",
                (delivery, notice, destination),
            )
            migrate("upgrade", "head")
            row = connection.execute(
                "SELECT status, next_attempt_at, window_id, batch_id, batch_index "
                "FROM notification_deliveries WHERE id = %s",
                (delivery,),
            ).fetchone()
            assert row == ("pending", 5, None, None, None)
            assert not connection.execute(
                "SELECT 1 FROM notification_aggregation_windows"
            ).fetchall()
            migrate("downgrade", PREVIOUS_HEAD)
            columns = {
                name
                for (name,) in connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'notification_deliveries' AND table_schema = %s",
                    (schema,),
                )
            }
            assert not columns & {"window_id", "batch_id", "batch_index"}
            migrate("upgrade", "head")
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
