"""Timing columns arrive without touching stored preferences, queued work or notifications."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.core.notification_timing_schema import DEFAULT_TIMING
from src.features import notification_timing_policy as timing

PREVIOUS_HEAD = "61de2c6d5459"
LEGACY_PREFERENCES = {
    "notify_episode_aired": False,
    "notification_types": {"media.movie.released": False},
    "notification_destinations": {"8d0d2f0c-0c51-4d63-a5a4-5d0c2d9b8a11": True},
    "notification_routes": {
        "media.episode.aired": {
            "8d0d2f0c-0c51-4d63-a5a4-5d0c2d9b8a11": {"enabled": True, "urgency": "critical"}
        }
    },
    "notification_retention_days": 90,
}


def test_timing_migration_preserves_preferences_notifications_and_queued_work():
    schema = f"notification_timing_migration_{uuid4().hex}"
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
            owner, notice, destination, delivery = uuid4(), uuid4(), uuid4(), uuid4()
            connection.execute(
                """INSERT INTO users (id, username, email, password_hash,
                   is_active, is_admin, created_at, updated_at)
                   VALUES (%s, %s, %s, 'unused', true, false, 1, 1)""",
                (owner, str(owner), f"{owner}@example.test"),
            )
            connection.execute(
                "INSERT INTO user_preferences (id, user_id, data) VALUES (%s, %s, %s::jsonb)",
                (uuid4(), owner, json.dumps(LEGACY_PREFERENCES)),
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
            stored = connection.execute(
                "SELECT data FROM user_preferences WHERE user_id = %s", (owner,)
            ).fetchone()[0]
            assert stored == LEGACY_PREFERENCES  # nothing reset, nothing rewritten
            assert connection.execute(
                "SELECT requested_urgency FROM notifications WHERE id = %s", (notice,)
            ).fetchone() == ("normal",)
            assert connection.execute(
                "SELECT status, next_attempt_at, timing_reason FROM notification_deliveries "
                "WHERE id = %s",
                (delivery,),
            ).fetchone() == ("pending", 5, None)
            migrate("downgrade", PREVIOUS_HEAD)
            assert (
                connection.execute(
                    "SELECT data FROM user_preferences WHERE user_id = %s", (owner,)
                ).fetchone()[0]
                == LEGACY_PREFERENCES
            )
            migrate("upgrade", "head")
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_legacy_preferences_without_timing_decide_exactly_like_the_defaults():
    facts = timing.TimingFacts(
        event_type="media.episode.aired",
        purpose="standard",
        category="media",
        destination_id=str(uuid4()),
        kind="episode_aired",
        route_urgency="critical",
    )
    legacy = timing.decide(facts, None, now=1_700_000_000)
    explicit = timing.decide(facts, DEFAULT_TIMING, now=1_700_000_000)
    assert legacy == explicit
    assert legacy.action == "deliver" and legacy.bypassed  # the pre-existing urgent bypass
