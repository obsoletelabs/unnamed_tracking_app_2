"""Exercise populated upgrades in an isolated schema through the actual Alembic CLI."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings


def test_populated_notification_upgrade_preserves_inbox_and_suppresses_unsafe_work():
    schema = f"notification_migration_{uuid4().hex}"
    base_url = make_url(settings.DATABASE_URL)
    connection_url = base_url.set(drivername="postgresql").render_as_string(hide_password=False)
    scoped_url = base_url.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = {**os.environ, "DATABASE_URL": scoped_url.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)
    command = [sys.executable, "-m", "alembic", "-c", "alembic.ini"]
    directory = Path(__file__).resolve().parents[1]
    user_id, notification_id, installation_id = [uuid4() for _ in range(3)]

    def migrate(*arguments):
        subprocess.run(
            command + list(arguments),
            cwd=directory,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

    with psycopg.connect(connection_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", "d8338e79fbbd")
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            connection.execute(
                """INSERT INTO users
                (id, username, email, password_hash, is_active, is_admin, created_at, updated_at)
                VALUES (%s, %s, %s, 'unused', true, false, 1, 1)""",
                (user_id, str(user_id), f"{user_id}@example.test"),
            )
            connection.execute(
                """INSERT INTO notifications
                (id, user_id, kind, media_type, media_id, title, body, event_at, dedupe_key,
                 read_at, created_at) VALUES (%s, %s, 'session_anomaly', 'system', %s,
                 'Old notice', 'Private content', 1, 'original-key', 2, 1)""",
                (notification_id, user_id, uuid4()),
            )
            connection.execute(
                """INSERT INTO plugin_notification_provider_registrations
                (id, plugin_id, installation_id, provider_id, name, action_id, registered_at)
                VALUES (%s, 'example.provider', %s, 'example.provider.webhook',
                        'Example', 'deliver', 1)""",
                (uuid4(), installation_id),
            )
            connection.execute(
                """INSERT INTO notification_provider_settings
                (id, user_id, provider_id, enabled, updated_at)
                VALUES (%s, %s, 'example.provider.webhook', true, 1)""",
                (uuid4(), user_id),
            )
            connection.execute(
                """INSERT INTO notification_deliveries
                (id, notification_id, provider_id, status, attempts, next_attempt_at)
                VALUES (%s, %s, 'example.provider.webhook', 'pending', 0, 1)""",
                (uuid4(), notification_id),
            )
            migrate("upgrade", "head")
            assert connection.execute(
                "SELECT accepted_at FROM notification_receipts"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT source_installation_id FROM notifications"
            ).fetchone() == (None,)
            assert all(
                connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]
                for table in ("game_note_details", "game_note_versions")
            )
            connection.execute(
                """INSERT INTO plugin_notification_type_registrations
                (id, plugin_id, installation_id, event_type, definition, registered_at)
                VALUES (%s, 'example.migration', %s, 'example.migration.notice', '{}', 1)""",
                (uuid4(), installation_id),
            )
            with pytest.raises(subprocess.CalledProcessError) as failure:
                migrate("downgrade", "24bc18feb852")
            assert "Cannot remove retained notification source identities" in failure.value.stderr
            connection.execute("DELETE FROM plugin_notification_type_registrations")
            assert connection.execute("""SELECT id, read_at, required_trust, purpose, body
                FROM notifications""").fetchone() == (
                notification_id,
                2,
                2,
                "security",
                "Private content",
            )
            assert connection.execute(
                "SELECT dedupe_key FROM notification_receipts"
            ).fetchone() == ("original-key",)
            assert connection.execute("""SELECT provider_id, status FROM notification_deliveries
                ORDER BY provider_id""").fetchall() == [
                ("core.inbox", "sent"),
                ("example.provider.webhook", "suppressed"),
            ]
            assert connection.execute("""SELECT privacy, installation_id
                FROM notification_destinations WHERE kind = 'legacy_webhook'""").fetchone() == (
                0,
                installation_id,
            )
            assert connection.execute(
                "SELECT DISTINCT requested_urgency, effective_urgency FROM notification_deliveries"
            ).fetchall() == [("normal", "normal")]
            email_destination = uuid4()
            connection.execute(
                """INSERT INTO notification_destinations
                (id, user_id, provider_id, endpoint_key, kind, channel_context, privacy,
                 revision, enabled, active, recovery_allowed, created_at)
                VALUES (%s, %s, 'core.smtp', 'test-email', 'email', 'external', 1,
                        1, true, true, false, 1)""",
                (email_destination, user_id),
            )
            with pytest.raises(subprocess.CalledProcessError) as failure:
                migrate("downgrade", "22ff87d4d01c")
            assert "Remove active email destinations" in failure.value.stderr
            connection.execute(
                "DELETE FROM notification_destinations WHERE id = %s", (email_destination,)
            )
            migrate("downgrade", "d8338e79fbbd")
            assert connection.execute("SELECT id, read_at FROM notifications").fetchone() == (
                notification_id,
                2,
            )
            assert connection.execute("SELECT status FROM notification_deliveries").fetchone() == (
                "skipped",
            )
        finally:
            connection.execute("SET search_path TO public")
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
