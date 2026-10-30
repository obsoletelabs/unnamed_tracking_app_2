"""API routes for listing and manually updating game achievements."""

import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Integer, exists, func, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import count as sql_count

from src.api.routes.utils.games import _get_game_or_404
from src.core.auth import get_current_user
from src.database.models.achievement import Achievement
from src.database.models.achievement_override import AchievementOverride
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import get_db
from src.features.achievement_folding import (
    CATALOG,
    apply_manual_progress,
    is_catalog,
    merge_copies,
)
from src.features.game_identity.copies import CopyLabel, page_copies

router = APIRouter(prefix="/api/game", tags=["game"], dependencies=[Depends(get_current_user)])
_DB_DEPENDENCY = Depends(get_db)
_CURRENT_USER_DEPENDENCY = Depends(get_current_user)


# A clock that is slightly ahead should not reject an earned date of "now".
FUTURE_TOLERANCE_SECONDS = 300


class AchievementProgressUpdate(BaseModel):
    unlocked: bool
    # Unix seconds the achievement was earned. Defaults to now when it is unlocked without one.
    unlocked_at: int | None = Field(default=None, ge=1, le=2**53 - 1)

    @model_validator(mode="after")
    def date_requires_unlock(self) -> "AchievementProgressUpdate":
        if self.unlocked_at is not None and not self.unlocked:
            raise ValueError("An earned date can only be set on an unlocked achievement")
        return self


@router.get("/achievements-summary")
async def get_achievements_summary(
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict[str, dict[str, int]]:
    """Return per-game achievement totals for the caller's active games."""
    synced = aliased(Achievement)
    rows = await db.execute(
        select(
            Achievement.game_id,
            sql_count(Achievement.id),
            func.sum(sql_cast(Achievement.unlocked, Integer)),
        )
        .join(Game, Game.id == Achievement.game_id)
        .where(
            Game.user_id == current_user.id,
            Game.deleted_at.is_(None),
            or_(
                Achievement.provider != CATALOG,
                ~exists().where(synced.game_id == Game.id, synced.provider != CATALOG),
            ),
        )
        .group_by(Achievement.game_id)
    )
    return {
        str(game_id): {"total": total, "unlocked": unlocked or 0}
        for game_id, total, unlocked in rows
    }


@router.get("/{game_id}/achievements")
async def list_game_achievements(
    game_id: UUID,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
    group: bool = False,
) -> list[dict]:
    """List stored progress and catalogue entries, applying personal overrides."""
    game = await _get_game_or_404(game_id, db, current_user.id)
    labels = await page_copies(db, current_user.id, game_id) if group else []
    if not labels:
        labels = [CopyLabel(game.id, game.title, game.source, game.platform, True)]
    ids = [copy.game_id for copy in labels]
    app_ids = {
        row.id: row.external_id
        for row in await db.execute(select(Game.id, Game.external_id).where(Game.id.in_(ids)))
    }
    result = await db.execute(
        select(Achievement)
        .where(Achievement.game_id.in_(ids))
        .order_by(Achievement.unlocked.desc(), Achievement.name)
    )
    rows = list(result.scalars().all())
    overrides = (
        await db.scalars(select(AchievementOverride).where(AchievementOverride.game_id.in_(ids)))
    ).all()
    rows = apply_manual_progress(rows, overrides)
    return merge_copies([(label, app_ids.get(label.game_id) or "") for label in labels], rows)


@router.put("/{game_id}/achievements/{achievement_id}/progress")
async def set_game_achievement_progress(
    game_id: UUID,
    achievement_id: UUID,
    progress: AchievementProgressUpdate,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict:
    """Set personal progress for a synced or Atlas catalogue achievement."""
    await _get_game_or_404(game_id, db, current_user.id)
    achievement = await db.scalar(
        select(Achievement).where(Achievement.id == achievement_id, Achievement.game_id == game_id)
    )
    if achievement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Achievement not found")

    if progress.unlocked_at is not None and progress.unlocked_at > int(time.time()) + (
        FUTURE_TOLERANCE_SECONDS
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The earned date cannot be in the future",
        )

    provider = CATALOG if is_catalog(achievement) else achievement.provider
    override = await db.scalar(
        select(AchievementOverride)
        .where(
            AchievementOverride.game_id == game_id,
            AchievementOverride.provider == provider,
            AchievementOverride.external_id == achievement.external_id,
        )
        .with_for_update()
    )
    if override is None:
        override = AchievementOverride(
            game_id=game_id,
            provider=provider,
            external_id=achievement.external_id,
            name=achievement.name[:500],
        )
        db.add(override)
    override.name = achievement.name[:500]
    override.description = achievement.description
    override.unlocked = progress.unlocked
    override.unlocked_at = (progress.unlocked_at or int(time.time())) if progress.unlocked else None
    override.updated_at = int(time.time())
    await db.commit()
    return {
        "unlocked": override.unlocked,
        "unlocked_at": override.unlocked_at,
        "manual_override": True,
    }


@router.delete("/{game_id}/achievements/{achievement_id}/progress")
async def clear_game_achievement_progress(
    game_id: UUID,
    achievement_id: UUID,
    db: AsyncSession = _DB_DEPENDENCY,
    current_user: User = _CURRENT_USER_DEPENDENCY,
) -> dict:
    """Remove a personal override and return to the latest synced progress."""
    await _get_game_or_404(game_id, db, current_user.id)
    achievement = await db.scalar(
        select(Achievement).where(Achievement.id == achievement_id, Achievement.game_id == game_id)
    )
    if achievement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Achievement not found")
    provider = CATALOG if is_catalog(achievement) else achievement.provider
    override = await db.scalar(
        select(AchievementOverride).where(
            AchievementOverride.game_id == game_id,
            AchievementOverride.provider == provider,
            AchievementOverride.external_id == achievement.external_id,
        )
    )
    if override is not None:
        await db.delete(override)
        await db.commit()
    return {"manual_override": False}
