"""Populated upgrades/adoption preserve notices and refuse destructive audit downgrade."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings


def test_lifecycle_migration_preserves_existing_data_and_adopts_without_replay():
    schema = f"notification_audit_migration_{uuid4().hex}"
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
            timeout=60,
        )
        assert (result.returncode == 0) is successful, result.stderr
        return result

    url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", "08cca40cdb7f")
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            owner, notice = uuid4(), uuid4()
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
                   VALUES (%s, %s, 'plugin', 'plugin', %s, 'Retained title', 'Retained body',
                   1, 'retained', 1, 'plugin.notice', 'example.retained', 1, 'standard',
                   'info', true)""",
                (notice, owner, uuid4()),
            )
            migrate("upgrade", "0037e34ac954")
            assert connection.execute(
                "SELECT title, body FROM notifications WHERE id = %s", (notice,)
            ).fetchone() == ("Retained title", "Retained body")
            assert connection.execute(
                "SELECT count(*) FROM notification_lifecycle_audit"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT count(*) FROM notification_lifecycle_outbox"
            ).fetchone() == (0,)
            assert connection.execute("SELECT to_regclass('game_note_details')").fetchone()[0]
            assert connection.execute("SELECT to_regclass('game_duplicate_dismissals')").fetchone()[0]
            migrate("stamp", "08cca40cdb7f")
            migrate("upgrade", "0037e34ac954")
            connection.execute(
                """INSERT INTO notification_lifecycle_outbox (id, user_id, plugin_id,
                   installation_id, notification_id, status, occurred_at)
                   VALUES (%s, %s, 'example.retained', %s, %s, 'deleted', 1)""",
                (uuid4(), owner, uuid4(), notice),
            )
            blocked = migrate("downgrade", "08cca40cdb7f", successful=False)
            assert "populated notification lifecycle history" in blocked.stderr
            assert connection.execute(
                "SELECT count(*) FROM notification_lifecycle_outbox"
            ).fetchone() == (1,)
            connection.execute("DELETE FROM notification_lifecycle_outbox")
            migrate("downgrade", "08cca40cdb7f")
            migrate("upgrade", "0037e34ac954")
        finally:
            connection.execute("SET search_path TO public")
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
