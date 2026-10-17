"""Per-revision deliveries and remote references arrive additively and reversibly."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings

PREVIOUS_HEAD = "b7d41c9a2e65"


def test_dispatch_migration_keeps_deliveries_and_is_reversible():
    schema = f"notification_dispatch_migration_{uuid4().hex}"
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

    def migrate(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr

    url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", PREVIOUS_HEAD)
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            owner, notice, delivery = uuid4(), uuid4(), uuid4()
            connection.execute(
                """INSERT INTO users (id, username, email, password_hash,
                   is_active, is_admin, created_at, updated_at)
                   VALUES (%s, %s, %s, 'unused', true, false, 1, 1)""",
                (owner, str(owner), f"{owner}@example.test"),
            )
            connection.execute(
                """INSERT INTO notifications (id, user_id, kind, media_type, media_id, title,
                   body, event_at, dedupe_key, created_at, event_type, source, required_trust,
                   purpose, severity, inbox_visible)
                   VALUES (%s, %s, 'episode_aired', 'tv', %s, 'T', 'B', 1, 'k', 1,
                   'media.episode.aired', 'host', 1, 'standard', 'info', true)""",
                (notice, owner, uuid4()),
            )
            connection.execute(
                """INSERT INTO notification_deliveries (id, notification_id, provider_id,
                   destination_revision, projection, status, attempts, next_attempt_at)
                   VALUES (%s, %s, 'core.inbox', 1, 'canonical', 'sent', 0, 1)""",
                (delivery, notice),
            )
            migrate("upgrade", "head")
            assert connection.execute(
                "SELECT story_revision, operation, fallback, status FROM notification_deliveries"
            ).fetchone() == (0, "send", None, "sent")
            migrate("downgrade", PREVIOUS_HEAD)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                    (schema,),
                )
            }
            assert "notification_remote_references" not in tables
            assert connection.execute("SELECT status FROM notification_deliveries").fetchone() == (
                "sent",
            )
            migrate("upgrade", "head")
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
