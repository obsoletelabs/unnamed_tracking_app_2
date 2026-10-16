"""Personal schedule for the existing AniList public-list import.

The username and "update existing" choice stay in the account's preferences (they are import
settings); whether it runs, how often and when it last ran live in `job_settings` like every
other personal task, so the same scheduler loop and Tasks controls drive it."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports.anilist import import_anilist_library
from src.features.jobs import JobSpec, record_run
from src.features.metadata.anime.anilist import AniListError

logger = logging.getLogger(__name__)
ANILIST_JOB_NAMESPACE = "anilist"
MIN_INTERVAL_MINUTES = 60
MAX_INTERVAL_MINUTES = 30 * 24 * 60
MAX_CONCURRENT_IMPORTS = 4
_running: dict[str, asyncio.Task[None]] = {}
_slots = asyncio.Semaphore(MAX_CONCURRENT_IMPORTS)
NEEDS_USERNAME = "Set your AniList username in Import & export to use this task."


def anilist_job_id(user_id: UUID) -> str:
    """One stable schedule per account, independent of the saved username."""
    return "user-anilist:" + str(uuid5(user_id, ANILIST_JOB_NAMESPACE))


def _summarize(result: dict[str, Any]) -> str:
    return str(result.get("summary") or "")[:512]


async def get_anilist_jobs(db: AsyncSession, user_id: UUID) -> list[JobSpec]:
    """Always list the task for an active account so the schedule is discoverable."""
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return []
    prefs = await load_preferences(db, user_id)
    reason = "" if str(prefs.get("anilist_import_username") or "").strip() else NEEDS_USERNAME
    return [_job_spec(user_id, reason)]


def _job_spec(user_id: UUID, reason: str) -> JobSpec:
    job_id = anilist_job_id(user_id)

    def is_running() -> bool:
        return job_id in _running

    def start(_mode: str) -> dict[str, Any]:
        if reason:
            return {"running": False, "detail": reason}
        if not is_running():
            _running[job_id] = asyncio.create_task(_execute(spec))
        return {"running": True}

    spec = JobSpec(
        id=job_id,
        name="AniList import",
        description="Import your public AniList anime list into this library. "
        "It only reads AniList and never changes it.",
        min_interval_minutes=MIN_INTERVAL_MINUTES,
        max_interval_minutes=MAX_INTERVAL_MINUTES,
        default_interval_minutes=24 * 60,
        default_enabled=False,
        manual_mode="all",
        start=start,
        is_running=is_running,
        summarize=_summarize,
        available=not reason,
        unavailable_reason=reason,
        user_id=user_id,
    )
    return spec


async def _execute(spec: JobSpec) -> None:
    result: dict[str, Any] = {"completed": False, "summary": "Import could not run. Try Run now."}
    try:
        async with _slots, SessionLocal() as db:
            user = await db.get(User, spec.user_id) if spec.user_id else None
            prefs = await load_preferences(db, user.id) if user else {}
            username = str(prefs.get("anilist_import_username") or "").strip()
            if user is None or not user.is_active:
                result["summary"] = "The account is unavailable. Import is paused."
            elif not username:
                result["summary"] = NEEDS_USERNAME
            else:
                counts = await import_anilist_library(
                    db, user.id, username, bool(prefs.get("anilist_import_update_existing"))
                )
                result = {
                    "completed": True,
                    **{k: int(counts[k]) for k in ("fetched", "created", "updated", "skipped")},
                }
                result["summary"] = (
                    f"{result['fetched']} fetched, {result['created']} added, "
                    f"{result['updated']} updated, {result['skipped']} skipped."
                )
    except AniListError:
        result["summary"] = "AniList could not be reached or the list is private. Try again later."
    # A failed provider or worker must not stop the host scheduler or expose remote details.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.error("Scheduled AniList import failed: %s", spec.id)
    finally:
        try:
            await record_run(spec.id, result, spec=spec)
        except SQLAlchemyError:
            logger.error("AniList import result could not be saved: %s", spec.id)
        finally:
            _running.pop(spec.id, None)
