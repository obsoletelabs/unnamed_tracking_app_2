"""Recurring server maintenance and personal account-library imports.

Server jobs are described in JOBS; personal adapters resolve the account's connections.
Each schedule is stored in `job_settings`;
a job starts on or off as its spec says (the airing check is on because new
episodes should appear without anyone asking, the full refresh is off because
it is heavy). One loop (started in main.py) wakes every minute, and runs any
enabled job that is due. Running a job by hand, from a screen or from
the loop, goes through the same code and records the same last-run details.
A failed run is recorded too, so it waits for its interval instead of being
retried on every tick, and its summary says what went wrong.

Adding a job is one entry here plus whatever it does; the Tasks screen lists
whatever is registered."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.metrics import JOB_DURATION, JOB_RUNS
from src.database.models.job_setting import JobSetting
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import job_runs
from src.features.anime_franchise import run_franchise_check
from src.features.backup.scheduler import run_all_backups
from src.features.calendar_sources import run_due_sources
from src.features.metadata import refresh_job
from src.features.metadata.refresh import check_airing_episodes
from src.features.notification_audit import publish_lifecycle
from src.features.notification_browser import expire_browser_sessions
from src.features.notification_enrollment import expire_verification_secrets
from src.features.notification_providers.delivery import process_pending_deliveries
from src.features.notification_scans import scan_notification_users
from src.features.password_recovery import expire_recovery_secrets
from src.features.trash.sweep import run_sweep_once
from src.features.user_invitations import expire_invitations

logger = logging.getLogger(__name__)
TICK_SECONDS = 60


@dataclass(frozen=True)
class JobSpec:
    # This descriptor is the Tasks UI/scheduling record, including its execution callbacks.
    # pylint: disable=too-many-instance-attributes

    """Execution adapter and schedule limits shared by built-in and plugin jobs."""

    id: str
    name: str
    description: str
    min_interval_minutes: int
    max_interval_minutes: int
    default_interval_minutes: int
    default_enabled: bool
    start: Callable[[str], dict[str, Any]]
    is_running: Callable[[], bool]
    summarize: Callable[[dict[str, Any]], str]
    manual_mode: str = "needed"
    plugin_id: str | None = None
    plugin_name: str | None = None
    available: bool = True
    unavailable_reason: str = ""
    user_id: UUID | None = None
    # Set when the cause is permanent (disconnect, revoked grant, disabled plugin) rather than an
    # outage: the owner's schedule is then turned off and must be explicitly enabled again.
    revoked: bool = False
    # Integration tasks retry a failed step with bounded backoff; built-in and AniList
    # schedules record the failure and wait for their interval instead.
    retry_backoff: bool = False


def personal_job_defaults() -> dict[str, Any]:
    """Shared defaults for opt-in personal schedules."""
    return {
        "default_interval_minutes": 24 * 60,
        "default_enabled": False,
        "manual_mode": "all",
    }


def _summarize_refresh(r: dict[str, Any]) -> str:
    parts = [
        f"{r.get('checked') or 0} refreshed",
        f"{r.get('skipped_up_to_date') or 0} already fine",
    ]
    if r.get("counts_fixed"):
        parts.append(f"{r['counts_fixed']} count(s) corrected")
    return ", ".join(parts)


def _summarize_airing(r: dict[str, Any]) -> str:
    if r.get("error"):
        return str(r["error"])
    added = (r.get("anime_episodes_added") or 0) + (r.get("tv_episodes_added") or 0)
    parts = [f"{r.get('checked') or 0} checked", f"{r.get('not_due') or 0} not due yet"]
    if added:
        parts.append(f"{added} new episode(s)")
    if r.get("failed"):
        parts.append(f"{r['failed']} unavailable; retried next run")
    return ", ".join(parts)


_running = {
    "airing_check": False,
    "plugin_updates": False,
    "anime_franchise_check": False,
    "library_backups": False,
    "trash_sweep": False,
    "calendar_subscriptions": False,
}


def _start_maintenance(
    job_id: str, work: Callable[[], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    """Run an async maintenance pass in the background, never overlapping itself."""
    if not _running[job_id]:
        _running[job_id] = True
        asyncio.get_running_loop().create_task(_run_maintenance(job_id, work))
    return {"running": True}


async def _maintenance_result(work: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    result = await work()
    if result.get("failed") or result.get("failed_parts"):
        result = {**result, "completed": False, "error": "Part of the run failed"}
    return result


async def _run_maintenance(job_id: str, work: Callable[[], Awaitable[dict[str, Any]]]) -> None:
    try:
        await _record_outcome(job_id, _maintenance_result(work), "The run failed")
    finally:
        _running[job_id] = False


def _summarize_backups(r: dict[str, Any]) -> str:
    text = f"{r.get('written') or 0} snapshot(s) written for {r.get('accounts') or 0} account(s)"
    return f"{text}, {r['failed']} failed" if r.get("failed") else text


def _summarize_sweep(r: dict[str, Any]) -> str:
    purged = sum(v for k, v in r.items() if k.startswith("purged_") and isinstance(v, int))
    text = f"{purged} expired item(s) purged, {r.get('expired_sessions_removed') or 0} session(s)"
    return f"{text}, {r['failed_parts']} step(s) failed" if r.get("failed_parts") else text


def _summarize_plugin_updates(r: dict[str, Any]) -> str:
    if r.get("error"):
        return str(r["error"])
    return f"{r.get('checked', 0)} checked, {r.get('installed', 0)} installed"


async def _record_outcome(job_id: str, work: Awaitable[dict[str, Any]], failure: str) -> None:
    """Record a failed run like a completed one, so cadence and reporting survive errors."""
    started = time.monotonic()
    try:
        result = await work
    # An unexpected worker failure must not terminate the remaining scheduled work.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("The scheduled job %s failed", job_id)
        result = {"completed": False, "error": failure}
    JOB_DURATION.labels(job_id if job_id in JOBS else "other").observe(time.monotonic() - started)
    try:
        await record_run(job_id, result)
    # Losing one result must not leave the job marked as running.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("The result of the scheduled job %s could not be saved", job_id)


def _plugin_updates_is_running() -> bool:
    return _running["plugin_updates"]


def _start_plugin_updates(_mode: str) -> dict[str, Any]:
    if not _running["plugin_updates"]:
        _running["plugin_updates"] = True
        asyncio.get_running_loop().create_task(_run_plugin_updates())
    return {"running": True}


async def _plugin_update_result() -> dict[str, Any]:
    # Keep importing job descriptors independent of Plugin Manager router initialization.
    # pylint: disable-next=import-outside-toplevel
    from src.api.routes.plugin_manager.lifecycle import run_automatic_plugin_updates

    async with SessionLocal() as db:
        admin = await db.scalar(
            select(User).where(User.is_admin.is_(True), User.is_active.is_(True))
        )
        return await run_automatic_plugin_updates(db, admin) if admin else {"checked": 0}


async def _run_plugin_updates() -> None:
    try:
        await _record_outcome(
            "plugin_updates",
            _plugin_update_result(),
            "Plugin updates could not finish. They will retry at the next scheduled time.",
        )
    finally:
        _running["plugin_updates"] = False


def _airing_is_running() -> bool:
    return _running["airing_check"]


def _start_airing(mode: str) -> dict[str, Any]:
    """Runs one airing check in the background. Scheduled runs only ask about
    shows that are due; "Run now" (mode "all") asks about every airing show."""
    if not _running["airing_check"]:
        _running["airing_check"] = True
        asyncio.get_running_loop().create_task(_run_airing(force=mode == "all"))
    return {"running": True}


async def _run_airing(force: bool) -> None:
    try:
        await _record_outcome(
            "airing_check",
            check_airing_episodes(force=force),
            "The airing check could not finish. It will retry at the next scheduled time.",
        )
    finally:
        _running["airing_check"] = False


def _franchise_check_is_running() -> bool:
    return _running["anime_franchise_check"]


def _start_franchise_check(_mode: str) -> dict[str, Any]:
    """Run one bounded batch; scheduled and manual runs both resume the saved cursor."""
    if not _running["anime_franchise_check"]:
        _running["anime_franchise_check"] = True
        asyncio.get_running_loop().create_task(_run_franchise_check())
    return {"running": True}


async def _franchise_check_result() -> dict[str, Any]:
    async with SessionLocal() as db:
        previous = dict((await get_setting(db, JOBS["anime_franchise_check"])).last_result or {})
        await db.commit()
    try:
        return await run_franchise_check(previous)
    # Keep the saved cursor so the next run resumes; the interval paces the retry.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("The anime franchise check failed")
        kept = ("cursor_user", "cursor_franchise", "stalled")
        resume = previous.get("completed") is False
        return {
            **({key: previous[key] for key in kept if key in previous} if resume else {}),
            "completed": False,
            "error": "The franchise check could not finish. It will resume at the next "
            "scheduled time.",
        }


async def _run_franchise_check() -> None:
    try:
        await _record_outcome(
            "anime_franchise_check",
            _franchise_check_result(),
            "The franchise check could not finish. It will resume at the next scheduled time.",
        )
    finally:
        _running["anime_franchise_check"] = False


JOBS: dict[str, JobSpec] = {
    "calendar_subscriptions": JobSpec(
        "calendar_subscriptions",
        "Calendar subscriptions",
        "Refresh due external calendars and expire removed events; each source keeps its own cadence.",
        1,
        1440,
        1,
        True,
        lambda _mode: _start_maintenance("calendar_subscriptions", run_due_sources),
        lambda: _running["calendar_subscriptions"],
        lambda r: f"{r.get('checked', 0)} checked, {r.get('failed', 0)} unavailable",
    ),
    "plugin_updates": JobSpec(
        "plugin_updates",
        "Plugin updates",
        "Checks catalogue releases and applies automatic update policy.",
        60,
        7 * 24 * 60,
        24 * 60,
        True,
        _start_plugin_updates,
        _plugin_updates_is_running,
        _summarize_plugin_updates,
    ),
    "airing_check": JobSpec(
        "airing_check",
        "Airing episode check",
        "Checks for newly aired episodes.",
        5,
        24 * 60,
        30,
        True,
        _start_airing,
        _airing_is_running,
        _summarize_airing,
        "all",
    ),
    "anime_franchise_check": JobSpec(
        "anime_franchise_check",
        "Anime franchise check",
        "Looks for newly listed seasons, movies, OVAs and specials of anime franchises "
        "completed by accounts that opted in. Each run checks a small batch and resumes "
        "where the previous one stopped.",
        60,
        30 * 24 * 60,
        24 * 60,
        True,
        _start_franchise_check,
        _franchise_check_is_running,
        lambda result: str(result.get("summary") or result.get("error") or "")[:512],
    ),
    "media_refresh": JobSpec(
        "media_refresh",
        "Media refresh",
        "Fills missing episode metadata and corrects stale media data.",
        60,
        30 * 24 * 60,
        24 * 60,
        False,
        refresh_job.start,
        refresh_job.is_running,
        _summarize_refresh,
    ),
    "library_backups": JobSpec(
        "library_backups",
        "Library backups",
        "Writes a rolling JSON snapshot of each account's library (the last 7 are kept).",
        60,
        7 * 24 * 60,
        24 * 60,
        True,
        lambda _mode: _start_maintenance("library_backups", run_all_backups),
        lambda: _running["library_backups"],
        _summarize_backups,
    ),
    "trash_sweep": JobSpec(
        "trash_sweep",
        "Trash and session cleanup",
        "Permanently removes trash past its retention period and expired sign-in sessions.",
        15,
        7 * 24 * 60,
        6 * 60,
        True,
        lambda _mode: _start_maintenance("trash_sweep", run_sweep_once),
        lambda: _running["trash_sweep"],
        _summarize_sweep,
    ),
}


def is_due(enabled: bool, last_run_at: int | None, interval_minutes: int, now: int) -> bool:
    """A disabled schedule is never due, including before its first run."""
    if not enabled:
        return False
    return last_run_at is None or now - last_run_at >= interval_minutes * 60


async def get_setting(db: AsyncSession, spec: JobSpec) -> JobSetting:
    """Seed defaults once while retaining this job owner's choices."""
    row = await db.get(JobSetting, spec.id)
    if row is None:
        row = JobSetting(
            job_id=spec.id,
            user_id=spec.user_id,
            enabled=spec.default_enabled,
            interval_minutes=spec.default_interval_minutes,
        )
        db.add(row)
        await db.flush()
    return row


async def describe(db: AsyncSession, spec: JobSpec) -> dict[str, Any]:
    """Combine persistent history with live runtime availability for Tasks controls."""
    row = await get_setting(db, spec)
    now = int(time.time())
    return {
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "enabled": row.enabled,
        "interval_minutes": row.interval_minutes,
        "min_interval_minutes": spec.min_interval_minutes,
        "max_interval_minutes": spec.max_interval_minutes,
        "last_run_at": row.last_run_at,
        "last_result": row.last_result or {},
        "last_summary": spec.summarize(row.last_result or {}) if row.last_run_at else "",
        "running": spec.is_running() or job_runs.lease_active(row, now),
        "paused": row.paused,
        "status": row.status,
        "last_success_at": row.last_success_at,
        "next_run_at": job_runs.next_run_at(row, now),
        "failure_count": row.failure_count,
        "plugin_id": spec.plugin_id,
        "plugin_name": spec.plugin_name,
        "available": spec.available,
        "unavailable_reason": spec.unavailable_reason,
    }


async def record_run(job_id: str, result: dict[str, Any], *, spec: JobSpec | None = None) -> None:
    """Persist bounded scalar results for built-in or installation-owned jobs."""
    async with SessionLocal() as db:
        spec = spec or JOBS[job_id]
        row = await get_setting(db, spec)
        now = int(time.time())
        row.last_run_at = now
        public = job_runs.apply_outcome(row, result, now, backoff=spec.retry_backoff)
        # Only the host's own job ids are label values; plugin and personal jobs share buckets.
        JOB_RUNS.labels(
            spec.id if spec.id in JOBS else ("plugin" if spec.plugin_id else "personal"),
            "success" if row.status == "completed" else row.status,
        ).inc()
        row.last_result = {
            k: v for k, v in public.items() if isinstance(v, (int, float, str, bool)) or v is None
        }
        await db.commit()


async def control_job(db: AsyncSession, spec: JobSpec, action: str) -> dict[str, Any]:
    """Pause, resume or cancel one job's run without touching its saved records."""
    row = await get_setting(db, spec)
    job_runs.apply_control(row, action, spec.is_running() or job_runs.lease_active(row))
    await db.commit()
    return await describe(db, spec)


async def disable_revoked_schedules(db: AsyncSession) -> None:
    """Turn off personal schedules whose connection, grant or plugin is permanently gone.

    Outages only pause a schedule; revocation, disconnect, disable and uninstall make the
    owner choose to enable it again, so nothing resumes silently when access returns."""
    # Adapters depend on JobSpec; import them after this registry is initialized.
    # pylint: disable-next=import-outside-toplevel
    from src.features import plugin_jobs

    rows = (
        await db.scalars(
            select(JobSetting).where(JobSetting.user_id.is_not(None), JobSetting.enabled.is_(True))
        )
    ).all()
    listed: dict[UUID, dict[str, JobSpec]] = {}
    for row in rows:
        user_id = row.user_id
        if user_id is None:
            continue
        if user_id not in listed:
            listed[user_id] = {spec.id: spec for spec in await get_user_jobs(db, user_id)}
        spec = listed[user_id].get(row.job_id)
        uninstalled = (
            spec is None
            and row.job_id.startswith("user-plugin:")
            and plugin_jobs.runtime_reachable()
        )
        if uninstalled or (spec is not None and spec.revoked):
            row.enabled, row.status, row.retry_at = False, "disabled", None
            row.last_result = {
                "completed": False,
                "summary": "Autosync was turned off because access was removed. "
                "Turn it on again after reconnecting or restoring permissions.",
            }
    await db.commit()


async def get_user_jobs(db: AsyncSession, user_id: UUID) -> list[JobSpec]:
    """Share the personal task registry between account controls and the scheduler."""
    # Adapters depend on JobSpec; import them after this registry is initialized.
    # pylint: disable=import-outside-toplevel
    from src.features.achievement_jobs import get_achievement_jobs
    from src.features.anilist_jobs import get_anilist_jobs
    from src.features.connection_jobs import get_connection_jobs
    from src.features.plugin_jobs import get_plugin_jobs

    return [
        *await get_connection_jobs(db, user_id),
        *await get_anilist_jobs(db, user_id),
        *await get_plugin_jobs(db, user_id),
        *await get_achievement_jobs(db, user_id),
    ]


async def get_scheduled_user_jobs(db: AsyncSession) -> list[JobSpec]:
    """Resolve only accounts that explicitly enabled a personal schedule."""
    users = await db.scalars(
        select(JobSetting.user_id)
        .where(JobSetting.user_id.is_not(None), JobSetting.enabled.is_(True))
        .distinct()
    )
    specs = []
    for user_id in users:
        if user_id is not None:
            specs.extend(await get_user_jobs(db, user_id))
    return specs


def _on_media_refresh_finished(progress: dict[str, Any]) -> None:
    asyncio.get_running_loop().create_task(record_run("media_refresh", progress))


refresh_job.finish_hooks.append(_on_media_refresh_finished)


async def due_jobs(db: AsyncSession, specs: list[JobSpec], now: int) -> list[JobSpec]:
    """Schedules whose next run (interval, a requested continuation of an unfinished batch, or
    a bounded retry after a failure) has arrived and that are not paused or leased. Due work
    is ordered oldest run first, so personal schedules waiting for the shared import slots are
    served by how long they have waited, across ticks and restarts."""
    due: list[tuple[int, JobSpec]] = []
    for spec in specs:
        row = await get_setting(db, spec)
        if (
            spec.available
            and spec.min_interval_minutes <= row.interval_minutes <= spec.max_interval_minutes
            and not spec.is_running()
            and job_runs.is_due(row, now)
        ):
            due.append((row.last_run_at or 0, spec))
    due.sort(key=lambda pair: pair[0])
    return [spec for _, spec in due]


async def run_jobs_loop() -> None:
    """Run eligible schedules without blocking imports or notification delivery."""
    # Plugin adapters import JobSpec/record_run from this registry; load them after initialization.
    # pylint: disable-next=import-outside-toplevel
    from src.features.plugin_jobs import get_plugin_jobs

    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            now = int(time.time())
            await scan_notification_users()
            async with SessionLocal() as db:
                await expire_verification_secrets(db)
                await expire_recovery_secrets(db)
                await expire_invitations(db)
                await expire_browser_sessions(db)
                await db.commit()
                await process_pending_deliveries(db)
                await publish_lifecycle(db)
                await disable_revoked_schedules(db)
                specs = [
                    *JOBS.values(),
                    *await get_plugin_jobs(db),
                    *await get_scheduled_user_jobs(db),
                ]
                due = await due_jobs(db, specs, now)
                # Queued additions must resume after a server restart even when the user has
                # left periodic autosync disabled. Scheduled runs still use the normal cadence.
                # pylint: disable=import-outside-toplevel
                from src.features.achievement_jobs import pending_job_specs

                pending_achievement_jobs = await pending_job_specs(db, now)
                await db.commit()
            for spec in due:
                logger.info("Starting the scheduled job %s", spec.id)
                spec.start("needed")
            due_ids = {spec.id for spec in due}
            for spec in pending_achievement_jobs:
                if spec.id not in due_ids:
                    logger.info("Resuming queued achievement work for %s", spec.user_id)
                    spec.start("queued")
        # An unexpected worker failure must not terminate the remaining scheduled work.
        # pylint: disable-next=broad-exception-caught
        except Exception:
            logger.exception("The jobs loop failed; it will try again")
