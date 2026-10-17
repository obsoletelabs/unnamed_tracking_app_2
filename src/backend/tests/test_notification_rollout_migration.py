"""Final rollout gate: the whole notification migration chain on populated and clean databases."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings

# The head of main before the notification overhaul (#106 to #117) added any schema.
PRE_OVERHAUL_HEAD = "e4a0157eef4b"
DIRECTORY = Path(__file__).resolve().parents[1]
EXPIRED_AT = 1_000


@contextmanager
def isolated_schema() -> Iterator[tuple[psycopg.Connection, object]]:
    schema = f"rollout_gate_{uuid4().hex}"
    base = make_url(settings.DATABASE_URL)
    environment = {
        **os.environ,
        "DATABASE_URL": base.update_query_dict(
            {"options": f"-csearch_path={schema}"}
        ).render_as_string(hide_password=False),
    }
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)

    def migrate(*arguments, successful=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
            cwd=DIRECTORY,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert (result.returncode == 0) is successful, result.stderr
        return result

    url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield connection, migrate
        finally:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )


def use(connection):
    """Point the data connection at the isolated schema created by the context manager."""
    schema = connection.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
    name = next(row[0] for row in schema if row[0].startswith("rollout_gate_"))
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(name)))


def populate(connection):
    """A representative pre-overhaul database: every record family the overhaul must keep."""
    owner, other = uuid4(), uuid4()
    for user in (owner, other):
        connection.execute(
            """INSERT INTO users (id, username, email, password_hash, is_active, is_admin,
               created_at, updated_at) VALUES (%s, %s, %s, 'unused', true, false, 1, 1)""",
            (user, str(user), f"{user}@example.test"),
        )
    connection.execute(
        "INSERT INTO user_preferences (id, user_id, data) VALUES (%s, %s, %s)",
        (
            uuid4(),
            owner,
            json.dumps(
                {"notification_retention_inherit": False, "notification_retention_days": 14}
            ),
        ),
    )
    notices = {}
    for name, read_at, expires_at, visible, dismissed_at in (
        ("unread", None, None, True, None),
        ("read", 50, None, True, None),
        ("dismissed", 60, None, True, 70),
        ("expired", None, EXPIRED_AT, False, None),
    ):
        notices[name] = uuid4()
        connection.execute(
            """INSERT INTO notifications (id, user_id, kind, media_type, media_id, title, body,
               event_at, dedupe_key, created_at, event_type, source, required_trust, purpose,
               severity, inbox_visible, read_at, expires_at, dismissed_at)
               VALUES (%s, %s, 'plugin_update', 'plugin', %s, %s, %s, 10, %s, 11,
               'system.plugin.update', 'host', 1, 'standard', 'info', %s, %s, %s, %s)""",
            (
                notices[name],
                owner,
                uuid4(),
                f"Title {name}",
                f"Body {name}",
                f"key-{name}",
                visible,
                read_at,
                expires_at,
                dismissed_at,
            ),
        )
        connection.execute(
            """INSERT INTO notification_receipts (id, user_id, dedupe_key, event_type, source,
               occurred_at, accepted_at, fingerprint)
               VALUES (%s, %s, %s, 'system.plugin.update', 'host', 10, 12, %s)""",
            (uuid4(), owner, f"key-{name}", f"fp-{name}"),
        )
    destination = uuid4()
    connection.execute(
        """INSERT INTO notification_destinations (id, user_id, provider_id, endpoint_key, kind,
           channel_context, privacy, revision, verified_revision, verification_method, enabled,
           active, recovery_allowed, media_consent_revision, media_consent_at, created_at,
           display_name)
           VALUES (%s, %s, 'core.smtp', 'owner@example.test', 'email', 'private', 1, 3, 3,
           'link', true, true, false, 3, 20, 5, 'Home')""",
        (destination, owner),
    )
    delivery = uuid4()
    connection.execute(
        """INSERT INTO notification_deliveries (id, notification_id, provider_id, status,
           attempts, attempted_at, next_attempt_at, destination_id, destination_revision,
           projection)
           VALUES (%s, %s, 'core.smtp', 'sent', 1, 30, 0, %s, 3, 'canonical')""",
        (delivery, notices["read"], destination),
    )
    connection.execute(
        """INSERT INTO notification_provider_settings (id, user_id, provider_id, enabled,
           updated_at) VALUES (%s, %s, 'core.browser', false, 40)""",
        (uuid4(), owner),
    )
    return owner, other, notices, destination, delivery


def snapshot(connection):
    return {
        "notifications": connection.execute(
            """SELECT id, user_id, title, body, read_at, dismissed_at, expires_at, inbox_visible,
               dedupe_key, created_at, event_type FROM notifications ORDER BY dedupe_key"""
        ).fetchall(),
        "receipts": connection.execute(
            "SELECT id, user_id, dedupe_key, fingerprint, accepted_at FROM notification_receipts"
            " ORDER BY dedupe_key"
        ).fetchall(),
        "destinations": connection.execute(
            """SELECT id, endpoint_key, verified_revision, verification_method, revision,
               media_consent_revision, media_consent_at, enabled FROM notification_destinations"""
        ).fetchall(),
        "deliveries": connection.execute(
            "SELECT id, notification_id, destination_id, status, attempts, destination_revision"
            " FROM notification_deliveries"
        ).fetchall(),
        "settings": connection.execute(
            "SELECT provider_id, enabled FROM notification_provider_settings"
        ).fetchall(),
        "preferences": connection.execute("SELECT data FROM user_preferences").fetchall(),
    }


def test_populated_upgrade_preserves_every_record_family_and_is_repeatable():
    with isolated_schema() as (connection, migrate):
        migrate("upgrade", PRE_OVERHAUL_HEAD)
        use(connection)
        _, _, notices, destination, delivery = populate(connection)
        before = snapshot(connection)
        migrate("upgrade", "head")
        assert snapshot(connection) == before  # IDs, read state, receipts, proof, retention intact
        # Rows that predate revisions are described as revision zero and normal urgency.
        assert connection.execute(
            "SELECT story_revision, operation, fallback FROM notification_deliveries WHERE id = %s",
            (delivery,),
        ).fetchone() == (0, "send", None)
        assert connection.execute(
            "SELECT DISTINCT requested_urgency FROM notifications"
        ).fetchall() == [("normal",)]
        assert connection.execute(
            "SELECT story_id, story_revision FROM notifications WHERE id = %s", (notices["read"],)
        ).fetchone() == (None, None)
        # A restart (or a second deploy) re-runs the migration without touching data.
        migrate("upgrade", "head")
        assert snapshot(connection) == before
        assert connection.execute(
            "SELECT count(*) FROM notification_destinations WHERE id = %s", (destination,)
        ).fetchone() == (1,)


def test_downgrade_is_refused_while_new_history_exists_and_lossless_otherwise():
    with isolated_schema() as (connection, migrate):
        migrate("upgrade", PRE_OVERHAUL_HEAD)
        use(connection)
        _, _, _, _, delivery = populate(connection)
        before = snapshot(connection)
        migrate("upgrade", "head")
        # Per-revision delivery history cannot be folded into one row per destination.
        connection.execute(
            "UPDATE notification_deliveries SET story_revision = 2 WHERE id = %s", (delivery,)
        )
        blocked = migrate("downgrade", "-1", successful=False)
        assert "per-revision notification deliveries" in blocked.stderr
        connection.execute(
            "UPDATE notification_deliveries SET story_revision = 0 WHERE id = %s", (delivery,)
        )
        # Non-default urgency history is likewise never dropped silently.
        connection.execute(
            "UPDATE notifications SET requested_urgency = 'high' WHERE dedupe_key = 'key-unread'"
        )
        migrate("downgrade", "-1")  # dispatch columns, no per-revision history
        migrate("downgrade", "-1")  # rich content columns hold no content
        blocked = migrate("downgrade", "-1", successful=False)
        assert "notification urgency history" in blocked.stderr
        connection.execute("UPDATE notifications SET requested_urgency = 'normal'")
        migrate("downgrade", PRE_OVERHAUL_HEAD)
        assert snapshot(connection) == before
        migrate("upgrade", "head")
        assert snapshot(connection) == before


def test_clean_database_reaches_the_single_head_with_every_notification_table():
    with isolated_schema() as (connection, migrate):
        migrate("upgrade", "head")
        use(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
            )
        }
        assert {
            "notifications",
            "notification_receipts",
            "notification_destinations",
            "notification_deliveries",
            "notification_delivery_attempts",
            "notification_provider_settings",
            "notification_stories",
            "notification_story_revisions",
            "notification_aggregation_windows",
            "notification_live_links",
            "notification_remote_references",
            "plugin_notification_type_registrations",
            "plugin_notification_provider_registrations",
        } <= tables
        heads = migrate("heads").stdout.strip().splitlines()
        assert len(heads) == 1 and heads[0].endswith("(head)")
