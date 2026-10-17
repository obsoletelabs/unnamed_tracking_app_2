"""move the AniList import schedule into personal job settings

Revision ID: a55b7c1d9e20
Revises: b87b7429ece2
Create Date: 2026-10-16 09:00:00.000000

"""

import json
import time
from typing import Sequence, Union
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a55b7c1d9e20"
down_revision: Union[str, None] = "b87b7429ece2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEDULE_KEYS = (
    "anilist_import_enabled",
    "anilist_import_interval_minutes",
    "anilist_import_last_run_at",
)
JOB_PREFIX = "user-anilist:"
MIN_INTERVAL, MAX_INTERVAL, DEFAULT_INTERVAL = 60, 30 * 24 * 60, 24 * 60


def _job_id(user_id: UUID) -> str:
    # Must match src.features.anilist_jobs.anilist_job_id.
    return JOB_PREFIX + str(uuid5(user_id, "anilist"))


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def upgrade() -> None:
    # Saved schedules keep their owner, enabled state, cadence and last run. Username and
    # "update existing" stay in preferences because they describe the import, not its schedule.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, user_id, data FROM user_preferences")).all()
    now = int(time.time())
    for pref_id, user_id, data in rows:
        data = data if isinstance(data, dict) else json.loads(data or "{}")
        if not any(key in data for key in SCHEDULE_KEYS):
            continue
        interval = _int_or_none(data.get("anilist_import_interval_minutes"))
        if interval is None or not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            interval = DEFAULT_INTERVAL
        last_run = _int_or_none(data.get("anilist_import_last_run_at"))
        bind.execute(
            sa.text(
                "INSERT INTO job_settings "
                "(job_id, user_id, enabled, interval_minutes, last_run_at, updated_at) "
                "VALUES (:job_id, :user_id, :enabled, :interval, :last_run, :now) "
                "ON CONFLICT (job_id) DO NOTHING"
            ),
            {
                "job_id": _job_id(user_id),
                "user_id": user_id,
                "enabled": data.get("anilist_import_enabled") is True,
                "interval": interval,
                "last_run": last_run,
                "now": now,
            },
        )
        remaining = {k: v for k, v in data.items() if k not in SCHEDULE_KEYS}
        bind.execute(
            sa.text("UPDATE user_preferences SET data = CAST(:data AS jsonb) WHERE id = :id"),
            {"data": json.dumps(remaining), "id": pref_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT job_id, user_id, enabled, interval_minutes, last_run_at FROM job_settings "
            "WHERE job_id LIKE :prefix AND user_id IS NOT NULL"
        ),
        {"prefix": JOB_PREFIX + "%"},
    ).all()
    for job_id, user_id, enabled, interval, last_run in rows:
        schedule = {
            "anilist_import_enabled": bool(enabled),
            "anilist_import_interval_minutes": interval,
            "anilist_import_last_run_at": last_run,
        }
        bind.execute(
            sa.text(
                "UPDATE user_preferences SET data = data || CAST(:schedule AS jsonb) "
                "WHERE user_id = :user_id"
            ),
            {"schedule": json.dumps(schedule), "user_id": user_id},
        )
        bind.execute(sa.text("DELETE FROM job_settings WHERE job_id = :job_id"), {"job_id": job_id})
