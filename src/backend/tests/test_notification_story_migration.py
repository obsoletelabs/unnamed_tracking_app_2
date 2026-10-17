"""Populated upgrade keeps notifications, read state and receipts; downgrade protects stories."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings

PREVIOUS_HEAD = "c10098e58426"


def test_story_migration_preserves_populated_notifications_and_receipts():
    schema = f"notification_story_migration_{uuid4().hex}"
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
            owner, read_notice, unread_notice = uuid4(), uuid4(), uuid4()
            connection.execute(
                """INSERT INTO users (id, username, email, password_hash,
                   is_active, is_admin, created_at, updated_at)
                   VALUES (%s, %s, %s, 'unused', true, false, 1, 1)""",
                (owner, str(owner), f"{owner}@example.test"),
            )
            for notice, key, read_at in (
                (read_notice, "k-read", 5),
                (unread_notice, "k-new", None),
            ):
                connection.execute(
                    """INSERT INTO notifications (id, user_id, kind, media_type, media_id,
                       title, body, event_at, dedupe_key, created_at, event_type, source,
                       required_trust, purpose, severity, inbox_visible, read_at)
                       VALUES (%s, %s, 'plugin_update', 'plugin', %s, 'Retained title',
                       'Retained body', 1, %s, 1, 'system.plugin.update', 'host', 1,
                       'standard', 'info', true, %s)""",
                    (notice, owner, uuid4(), key, read_at),
                )
                connection.execute(
                    """INSERT INTO notification_receipts (id, user_id, dedupe_key, event_type,
                       source, occurred_at, accepted_at, fingerprint)
                       VALUES (%s, %s, %s, 'system.plugin.update', 'host', 1, 1, 'abc')""",
                    (uuid4(), owner, key),
                )
            migrate("upgrade", "head")
            rows = connection.execute(
                """SELECT id, title, body, read_at, dedupe_key, story_id, story_revision,
                   previous_notification_id FROM notifications ORDER BY dedupe_key"""
            ).fetchall()
            assert rows == [
                (unread_notice, "Retained title", "Retained body", None, "k-new", None, None, None),
                (read_notice, "Retained title", "Retained body", 5, "k-read", None, None, None),
            ]
            assert connection.execute(
                "SELECT dedupe_key, fingerprint, story_key, story_phase FROM notification_receipts"
                " ORDER BY dedupe_key"
            ).fetchall() == [("k-new", "abc", None, None), ("k-read", "abc", None, None)]
            assert connection.execute("SELECT count(*) FROM notification_stories").fetchone() == (
                0,
            )
            # The empty story schema downgrades cleanly and upgrades again without loss.
            migrate("downgrade", PREVIOUS_HEAD)
            assert connection.execute("SELECT count(*) FROM notifications").fetchone() == (2,)
            migrate("upgrade", "head")
            story = uuid4()
            connection.execute(
                """INSERT INTO notification_stories (id, user_id, event_type, scope_key,
                   story_key, source, phase, revision, created_at, updated_at)
                   VALUES (%s, %s, 'system.plugin.update', 'host', 'k', 'host', 'available',
                   1, 1, 1)""",
                (story, owner),
            )
            connection.execute(
                "UPDATE notifications SET story_id = %s, story_revision = 1 WHERE id = %s",
                (story, unread_notice),
            )
            blocked = migrate("downgrade", PREVIOUS_HEAD, successful=False)
            assert "populated notification story history" in blocked.stderr
            assert connection.execute(
                "SELECT story_revision FROM notifications WHERE id = %s", (unread_notice,)
            ).fetchone() == (1,)
        finally:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )
