"""Personal achievement refresh jobs and durable game-addition work."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.achievement_sync_queue import AchievementSyncQueue
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.achievement_sync_queue import enqueue_user_games as enqueue_games
from src.features.job_runs import job_lease, lease_active
from src.features.jobs import JobSpec, get_setting, personal_job_defaults, record_run

logger = logging.getLogger(__name__)
MAX_GAMES_PER_RUN = 20
CONTINUE_AFTER_SECONDS = 60
_running: dict[str, asyncio.Task[None]] = {}


def achievement_job_id(user_id: UUID) -> str:
    """Keep the personal task identifier stable across app restarts."""
    return "user-ach:" + str(uuid5(user_id, "achievements"))


async def get_achievement_jobs(db: AsyncSession, user_id: UUID) -> list[JobSpec]:
    """Expose one opt-in scheduled task for each active account."""
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return []
    return [_job_spec(user_id)]


def _job_spec(user_id: UUID) -> JobSpec:
    job_id = achievement_job_id(user_id)

    def is_running() -> bool:
        return job_id in _running

    def start(mode: str) -> dict[str, Any]:
        if not is_running():
            _running[job_id] = asyncio.create_task(_execute(spec, mode))
        return {"running": True}

    spec = JobSpec(
        id=job_id,
        name="Achievements sync",
        description=(
            "Refresh achievement progress and metadata catalogues for your games. "
            "Newly added games are queued automatically."
        ),
        min_interval_minutes=60,
        max_interval_minutes=30 * 24 * 60,
        **personal_job_defaults(),
        start=start,
        is_running=is_running,
        summarize=lambda result: str(result.get("summary") or "")[:512],
        user_id=user_id,
        retry_backoff=True,
    )
    return spec


async def pending_job_specs(db: AsyncSession, now: int) -> list[JobSpec]:
    """Find durable queued work left by a process restart and make it runnable."""
    user_ids = await db.scalars(select(AchievementSyncQueue.user_id).distinct())
    specs: list[JobSpec] = []
    for user_id in user_ids:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            continue
        spec = _job_spec(user_id)
        row = await get_setting(db, spec)
        if (
            not spec.is_running()
            and not row.paused
            and not row.cancel_requested
            and row.status not in {"cancelled", "paused"}
            and (row.retry_at is None or row.retry_at <= now)
            and not lease_active(row, now)
        ):
            specs.append(spec)
    return specs


async def _execute(spec: JobSpec, mode: str) -> None:
    continue_work = False
    try:
        async with job_lease(spec) as acquired:
            if acquired:
                result, continue_work = await _run_locked(spec, mode)
                await record_run(spec.id, result, spec=spec)
    except SQLAlchemyError:
        logger.error("Achievement sync could not claim its run: %s", spec.id)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Achievement sync failed: %s", spec.id)
        try:
            await record_run(
                spec.id,
                {
                    "completed": False,
                    "summary": "Achievement sync failed. Check the connection and try again.",
                },
                spec=spec,
            )
        except SQLAlchemyError:
            logger.error("Achievement sync result could not be saved: %s", spec.id)
    finally:
        _running.pop(spec.id, None)
    if continue_work:
        asyncio.create_task(_continue(spec))


async def _continue(spec: JobSpec) -> None:
    await asyncio.sleep(2)
    spec.start("queued")


async def _run_locked(spec: JobSpec, mode: str) -> tuple[dict[str, Any], bool]:
    checked = synced = failed = 0
    should_continue = False
    try:
        async with SessionLocal() as db:
            if spec.user_id is None:
                return {"completed": False, "summary": "The account is unavailable."}, False
            user = await db.get(User, spec.user_id)
            if user is None or not user.is_active:
                return {"completed": False, "summary": "The account is unavailable."}, False
            user_id = user.id
            setting = await get_setting(db, spec)
            if mode == "needed" and not setting.enabled:
                return {
                    "completed": True,
                    "summary": "Autosync is disabled. Enable the schedule to run it automatically.",
                }, False
            if setting.paused or setting.cancel_requested:
                return {"completed": True, "summary": "This task is paused."}, False

            if mode != "queued":
                await enqueue_games(db, user_id)
                await db.commit()

            queued_game_ids = list(
                (
                    await db.scalars(
                        select(AchievementSyncQueue.game_id)
                        .where(AchievementSyncQueue.user_id == user_id)
                        .order_by(AchievementSyncQueue.queued_at, AchievementSyncQueue.game_id)
                        .limit(MAX_GAMES_PER_RUN)
                    )
                ).all()
            )
            # Import here so importing the task registry does not initialize API routers.
            # pylint: disable=import-outside-toplevel
            from src.api.routes.library_sync import refresh_game_achievements

            for game_id in queued_game_ids:
                await db.refresh(setting)
                if setting.paused or setting.cancel_requested:
                    break
                game = await db.get(Game, game_id)
                if game is None or game.user_id != user_id or game.deleted_at is not None:
                    await db.execute(
                        delete(AchievementSyncQueue).where(
                            AchievementSyncQueue.game_id == game_id
                        )
                    )
                    await db.commit()
                    continue
                checked += 1
                try:
                    current_user = await db.get(User, user_id)
                    if current_user is None or not current_user.is_active:
                        return {
                            "completed": False,
                            "summary": "The account is unavailable.",
                        }, False
                    await refresh_game_achievements(
                        game_id, db=db, current_user=current_user
                    )
                except HTTPException as exc:
                    await db.rollback()
                    await db.execute(
                        update(AchievementSyncQueue)
                        .where(AchievementSyncQueue.game_id == game_id)
                        .values(
                            attempts=AchievementSyncQueue.attempts + 1,
                            last_error=f"HTTP {exc.status_code}",
                            queued_at=int(time.time()),
                        )
                    )
                    await db.commit()
                    failed += 1
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("Achievement refresh failed for game %s", game_id)
                    await db.rollback()
                    await db.execute(
                        update(AchievementSyncQueue)
                        .where(AchievementSyncQueue.game_id == game_id)
                        .values(
                            attempts=AchievementSyncQueue.attempts + 1,
                            last_error="Unexpected refresh error",
                            queued_at=int(time.time()),
                        )
                    )
                    await db.commit()
                    failed += 1
                else:
                    await db.execute(
                        delete(AchievementSyncQueue).where(
                            AchievementSyncQueue.game_id == game_id
                        )
                    )
                    await db.commit()
                    synced += 1

            remaining = int(
                await db.scalar(
                    select(func.count())
                    .select_from(AchievementSyncQueue)
                    .where(AchievementSyncQueue.user_id == user_id)
                )
                or 0
            )
            should_continue = remaining > 0 and failed == 0 and checked == MAX_GAMES_PER_RUN
    except SQLAlchemyError:
        logger.exception("Achievement sync database work failed: %s", spec.id)
        return {
            "completed": False,
            "summary": "Achievement sync could not save its progress. It will retry.",
        }, False

    complete = remaining == 0
    summary = f"{synced} game(s) refreshed"
    if failed:
        summary += f", {failed} failed and remain queued for retry"
    if remaining:
        summary += f", {remaining} game(s) still queued"
    result: dict[str, Any] = {
        "completed": complete,
        "checked": checked,
        "synced": synced,
        "failed": failed,
        "queued": remaining,
        "summary": summary + ".",
    }
    if should_continue:
        result["continue_after_seconds"] = CONTINUE_AFTER_SECONDS
    return result, should_continue
