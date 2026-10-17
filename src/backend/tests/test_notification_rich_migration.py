"""Rich content storage and live links arrive additively and keep existing stories intact."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings

PREVIOUS_HEAD = "a3c81e5d7f20"


def test_rich_content_migration_keeps_existing_revisions_and_is_reversible():
    schema = f"notification_rich_migration_{uuid4().hex}"
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
            owner, story = uuid4(), uuid4()
            connection.execute(
                """INSERT INTO users (id, username, email, password_hash,
                   is_active, is_admin, created_at, updated_at)
                   VALUES (%s, %s, %s, 'unused', true, false, 1, 1)""",
                (owner, str(owner), f"{owner}@example.test"),
            )
            connection.execute(
                """INSERT INTO notification_stories (id, user_id, event_type, scope_key,
                   story_key, source, phase, revision, created_at, updated_at)
                   VALUES (%s, %s, 'system.plugin.update', 'host', 'k', 'host', 'available',
                   1, 1, 1)""",
                (story, owner),
            )
            connection.execute(
                """INSERT INTO notification_story_revisions (id, story_id, revision, phase,
                   decision, receipt_key, occurred_at, accepted_at, title, body)
                   VALUES (%s, %s, 1, 'available', 'create_story', 'r', 1, 1, 'T', 'B')""",
                (uuid4(), story),
            )
            migrate("upgrade", "head")
            assert connection.execute(
                "SELECT title, body, content FROM notification_story_revisions"
            ).fetchone() == ("T", "B", None)
            connection.execute(
                """INSERT INTO notification_live_links (id, story_id, user_id, token_hash,
                   created_at, expires_at) VALUES (%s, %s, %s, %s, 1, 2)""",
                (uuid4(), story, owner, "a" * 64),
            )
            migrate("downgrade", PREVIOUS_HEAD)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                    (schema,),
                )
            }
            assert "notification_live_links" not in tables
            assert connection.execute(
                "SELECT title, body FROM notification_story_revisions"
            ).fetchone() == ("T", "B")
            migrate("upgrade", "head")
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
