"""Run state for scheduled jobs: overlap leases, pause/cancel, retry backoff and next run.

Every personal or server job already persists its schedule in `job_settings`. This module adds
the durable run state to the same row so that overlap protection, checkpoints and controls
survive navigation, worker restarts and several host processes, without a second scheduler."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from src.database.models.job_setting import JobSetting
from src.database.session import SessionLocal

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 25
MAX_RETRIES = 5
BACKOFF_SECONDS = (60, 120, 240, 480, 900)
MIN_RETRY_AFTER, MAX_RETRY_AFTER = 60, 3600
CONTROLS = ("pause", "resume", "cancel")


def bounded_delay(value: Any) -> int | None:
    """Accept only a plain integer delay and keep it within the host's polling bounds."""
    if isinstance(value, int) and not isinstance(value, bool):
        return min(MAX_RETRY_AFTER, max(MIN_RETRY_AFTER, value))
    return None


async def _heartbeat(job_id: str, token: str) -> None:
    """Extend the lease while a long step runs; a lost lease stops the extension."""
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            async with SessionLocal() as db:
                result = await db.execute(
                    update(JobSetting)
                    .where(JobSetting.job_id == job_id, JobSetting.lease_token == token)
                    .values(lease_expires_at=int(time.time()) + LEASE_SECONDS)
                )
                await db.commit()
            if result.rowcount == 0:  # type: ignore[attr-defined]
                return
        except SQLAlchemyError:
            logger.warning("Job lease could not be extended: %s", job_id)


@asynccontextmanager
async def job_lease(spec: Any) -> AsyncIterator[bool]:
    """Claim one run of a job across processes; yields False when another run holds it.

    The claim is a single conditional UPDATE, so a manual start and a scheduled start racing
    each other cannot both proceed. A crashed process leaves a lease that expires on its own."""
    job_id, token, now = spec.id, uuid.uuid4().hex, int(time.time())
    async with SessionLocal() as db:
        await db.execute(  # a job that has never been configured still needs its row
            insert(JobSetting)
            .values(
                job_id=job_id,
                user_id=spec.user_id,
                enabled=spec.default_enabled,
                interval_minutes=spec.default_interval_minutes,
            )
            .on_conflict_do_nothing()
        )
        claimed = await db.execute(
            update(JobSetting)
            .where(
                JobSetting.job_id == job_id,
                or_(JobSetting.lease_expires_at.is_(None), JobSetting.lease_expires_at < now),
            )
            .values(lease_token=token, lease_expires_at=now + LEASE_SECONDS)
        )
        await db.commit()
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        yield False
        return
    beat = asyncio.create_task(_heartbeat(job_id, token))
    try:
        yield True
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat
        try:
            async with SessionLocal() as db:
                await db.execute(
                    update(JobSetting)
                    .where(JobSetting.job_id == job_id, JobSetting.lease_token == token)
                    .values(lease_token=None, lease_expires_at=None)
                )
                await db.commit()
        except SQLAlchemyError:
            logger.error("Job lease could not be released: %s", job_id)


def lease_active(row: JobSetting, now: int | None = None) -> bool:
    """Whether any process currently holds this job's run lease."""
    return bool(row.lease_expires_at and row.lease_expires_at >= (now or int(time.time())))


def apply_outcome(row: JobSetting, result: dict[str, Any], now: int) -> dict[str, Any]:
    """Update run health from one step's result and return the bounded public result.

    A result without `completed` (the built-in jobs) counts as a success. A step that saved a
    checkpoint continues quickly; a failure backs off, honouring a provider's `retry_after_seconds`
    and never retrying forever."""
    public = dict(result)
    completed = result.get("completed", True) is not False
    row.retry_at = None
    if result.get("disable_schedule") is True:
        row.enabled, row.status, row.failure_count = False, "disabled", 0
        public.pop("continue_after_seconds", None)
    elif row.cancel_requested:
        row.cancel_requested, row.status, row.failure_count = False, "cancelled", 0
        public.update(completed=False, summary="Cancelled. Saved progress was kept.")
        public.pop("continue_after_seconds", None)
    elif completed:
        row.last_success_at, row.status, row.failure_count = now, "completed", 0
    elif bounded_delay(result.get("continue_after_seconds")) is not None:
        row.status, row.failure_count = "partial", 0
    else:
        row.failure_count += 1
        row.status = "failed"
        if row.failure_count <= MAX_RETRIES:
            backoff = BACKOFF_SECONDS[min(row.failure_count, len(BACKOFF_SECONDS)) - 1]
            row.retry_at = now + max(backoff, bounded_delay(result.get("retry_after_seconds")) or 0)
    if row.paused and row.status not in {"disabled", "cancelled"}:
        row.status = "paused"
    return public


def next_run_at(row: JobSetting, now: int) -> int | None:
    """When the scheduler will next start this job, or None when nothing is scheduled."""
    if not row.enabled or row.paused:
        return None
    if row.retry_at:
        return row.retry_at
    if row.last_run_at is None:
        return now
    delay = row.interval_minutes * 60
    result = row.last_result or {}
    continuation = bounded_delay(result.get("continue_after_seconds"))
    # Only a personal task may resume a batch sooner; administrator and plugin server
    # tasks keep their configured interval, as before.
    if row.user_id is not None and result.get("completed") is False and continuation is not None:
        delay = continuation
    return row.last_run_at + delay


def is_due(row: JobSetting, now: int) -> bool:
    """Scheduler gate: enabled, not paused, not leased and past its next run."""
    when = next_run_at(row, now)
    return when is not None and when <= now and not lease_active(row, now)


def apply_control(row: JobSetting, action: str, running: bool) -> None:
    """Pause stops new steps, resume allows them, cancel ends the run but keeps records."""
    if action == "pause":
        row.paused = True
        row.status = "paused"
    elif action == "resume":
        row.paused = False
        row.retry_at = None
        row.failure_count = 0
        row.status = "idle"
    elif action == "cancel":
        row.retry_at = None
        if running:
            row.cancel_requested = True
        elif (row.last_result or {}).get("completed") is False:
            result = dict(row.last_result or {})
            result.pop("continue_after_seconds", None)
            row.last_result = {
                **result,
                "completed": False,
                "summary": "Cancelled. Saved progress was kept.",
            }
            row.status = "cancelled"
