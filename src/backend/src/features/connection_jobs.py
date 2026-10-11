"""Personal schedules for the host's existing account-library sync workflows."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.job_setting import JobSetting
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.jobs import JobSpec, get_setting, record_run

logger = logging.getLogger(__name__)
CONNECTIONS = {"steam": "Steam", "psn": "PlayStation", "retroachievements": "RetroAchievements"}
_running: dict[str, asyncio.Task[None]] = {}
_sync_owners: dict[tuple[UUID, str], asyncio.Task[Any] | None] = {}


def connection_job_id(user_id: UUID, provider: str) -> str:
    """Keep the schedule and history stable across disconnects and credential changes."""
    return "user-sync:" + str(uuid5(user_id, provider))


@asynccontextmanager
async def sync_scope(user_id: UUID, provider: str) -> AsyncIterator[None]:
    """Serialize inventory imports; the background workflow can call its own sync route."""
    key, owner = (user_id, provider), asyncio.current_task()
    if key in _sync_owners:
        if _sync_owners[key] is not owner:
            raise HTTPException(status_code=409, detail="This connection is already syncing.")
        yield
        return
    _sync_owners[key] = owner
    try:
        yield
    finally:
        _sync_owners.pop(key, None)


def _configured(user: User, provider: str) -> bool:
    if provider == "steam":
        return bool(
            user.steam_id
            and (user.steam_api_key or (user.steam_refresh_token and user.steam_access_token))
        )
    if provider == "psn":
        return bool(user.psn_npsso_token)
    return bool(user.retroachievements_username and user.retroachievements_api_key)


async def get_connection_jobs(db: AsyncSession, user_id: UUID) -> list[JobSpec]:
    """Expose configured connections and retain visibly paused schedules after disconnect."""
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return []
    saved = set(await db.scalars(select(JobSetting.job_id).where(JobSetting.user_id == user_id)))
    specs = []
    for provider, label in CONNECTIONS.items():
        configured = _configured(user, provider)
        if configured or connection_job_id(user_id, provider) in saved:
            reason = "" if configured else f"Reconnect {label} in Connections to resume syncing."
            specs.append(_job_spec(user_id, provider, label, reason))
    return specs


def _job_spec(user_id: UUID, provider: str, label: str, reason: str) -> JobSpec:
    job_id = connection_job_id(user_id, provider)

    def is_running() -> bool:
        return job_id in _running or (user_id, provider) in _sync_owners

    def start(mode: str) -> dict[str, Any]:
        if reason:
            return {"running": False, "detail": reason}
        if not is_running():
            _running[job_id] = asyncio.create_task(_execute(spec, provider, mode))
        return {"running": True}

    spec = JobSpec(
        id=job_id,
        name=f"{label} autosync",
        description=f"Sync your connected {label} library and achievement progress. "
        "Existing notes, ratings and manual status use the same protections as Sync / import.",
        min_interval_minutes=5,
        max_interval_minutes=7 * 24 * 60,
        default_interval_minutes=24 * 60,
        default_enabled=False,
        manual_mode="all",
        start=start,
        is_running=is_running,
        summarize=lambda result: str(result.get("summary") or "")[:512],
        available=not reason,
        unavailable_reason=reason,
        user_id=user_id,
    )
    return spec


async def _execute(spec: JobSpec, provider: str, mode: str) -> None:
    result: dict[str, Any] = {"completed": False, "summary": "Sync could not run. Try Run now."}
    try:
        async with SessionLocal() as db:
            user = await db.get(User, spec.user_id)
            if user is None or not user.is_active or not _configured(user, provider):
                result["summary"] = "The account or connection is unavailable. Sync is paused."
            elif mode == "needed" and not (await get_setting(db, spec)).enabled:
                result["summary"] = "Autosync was disabled before this run started."
            else:
                async with sync_scope(user.id, provider):
                    counts = await _sync_library(db, user, provider)
                result = {
                    "completed": True,
                    "achievement_games_unavailable": len(
                        counts.get("achievements_unavailable") or []
                    ),
                    "wishlist_failed": bool(counts.get("wishlist_failed")),
                    **{
                        key: int(counts.get(key) or 0)
                        for key in (
                            "games_added",
                            "games_updated",
                            "achievements_synced",
                            "enrich_failed",
                        )
                    },
                }
                result["summary"] = (
                    f"{result['games_added']} games added, {result['games_updated']} updated, "
                    f"{result['achievements_synced']} achievements synced."
                )
                if result["enrich_failed"]:
                    result["summary"] += f" {result['enrich_failed']} metadata updates unavailable."
                if result["achievement_games_unavailable"]:
                    amount = result["achievement_games_unavailable"]
                    result["summary"] += (
                        f" Progress unavailable for {amount} game{'s' if amount != 1 else ''}; "
                        "saved achievements kept."
                    )
                if result["wishlist_failed"]:
                    result["summary"] += " Wishlist unavailable."
    except HTTPException:
        result["summary"] = "Sync failed. Check the connection and try Run now."
    # A failed provider or worker must not stop the host scheduler or expose remote secrets.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.error("Connection sync failed: %s", spec.id)
    finally:
        try:
            await record_run(spec.id, result, spec=spec)
        except SQLAlchemyError:
            logger.error("Connection sync result could not be saved: %s", spec.id)
        finally:
            _running.pop(spec.id, None)


async def _sync_library(db: AsyncSession, user: User, provider: str) -> dict[str, Any]:
    """Reuse existing import workflows, including Steam wishlist and metadata follow-up."""
    # These established workflows still live beside their routes. Keep one implementation
    # and defer router imports until dispatch, as the plugin-update job does.
    # pylint: disable=import-outside-toplevel
    from src.api.routes import library_sync, steam_import_steps

    if provider == "psn":
        return await library_sync.sync_psn_library(db=db, current_user=user)
    if provider == "retroachievements":
        return await library_sync.sync_retroachievements_library(db=db, current_user=user)
    result = await library_sync.sync_steam_library(db=db, current_user=user, achievements="now")
    ids = list(result.get("enrich_game_ids") or [])
    try:
        wishlist = await steam_import_steps.import_wishlist(db=db, current_user=user)
        ids.extend(wishlist.get("game_ids") or [])
    except HTTPException:
        await db.rollback()
        await db.refresh(user)
        result["wishlist_failed"] = True
    result["enrich_failed"] = 0
    ids = list(dict.fromkeys(ids))
    for offset in range(0, len(ids), 5):
        batch = await steam_import_steps.enrich_games(
            steam_import_steps.EnrichRequest(game_ids=ids[offset : offset + 5]),
            db=db,
            current_user=user,
        )
        result["enrich_failed"] += batch["failed"]
    return result
