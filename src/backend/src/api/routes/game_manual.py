"""Manual achievements and completion alongside imported data (#97)."""

import asyncio
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.image_responses import original_image_response
from src.api.routes.game_identity import Db, Owner, _Body, _run
from src.core.auth import get_current_user
from src.database.models.game import GameStatus
from src.database.models.game_identity_tracking import GameManualAchievement
from src.database.session import get_db
from src.features.game_identity import manual
from src.helpers.image_prefetch import ICON_ROOT, WIDTHS
from src.helpers.remote_images import RemoteImageError, fetch_and_store, icon_cache_path

router = APIRouter(prefix="/api/game-identity", tags=["game"])

_Title = Annotated[str, Field(min_length=1, max_length=500)]
_Time = Annotated[int | None, Field(ge=0, le=253_402_300_799)]


class AchievementCreate(_Body):
    title: _Title
    description: Annotated[str | None, Field(max_length=20000)] = None
    icon_url: Annotated[str | None, Field(max_length=2000)] = None
    category: Annotated[str | None, Field(max_length=100)] = None
    position: Annotated[int, Field(ge=0, le=1_000_000)] = 0
    match_key: Annotated[str | None, Field(max_length=200)] = None
    achieved: bool = False
    achieved_at: _Time = None
    scope: Literal["version", "umbrella"] = "version"


class AchievementUpdate(_Body):
    title: _Title | None = None
    description: Annotated[str | None, Field(max_length=20000)] = None
    icon_url: Annotated[str | None, Field(max_length=2000)] = None
    category: Annotated[str | None, Field(max_length=100)] = None
    position: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    match_key: Annotated[str | None, Field(max_length=200)] = None
    achieved: bool | None = None
    achieved_at: _Time = None


class EarnedInput(_Body):
    achieved: bool
    achieved_at: _Time = None


class CompletionInput(_Body):
    status: GameStatus


@router.get("/games/{game_id}/tracking")
async def tracking(game_id: UUID, db: Db, user: Owner) -> dict[str, Any]:
    """Completion and achievements of an identity group, manual and imported."""
    return await _run(db, manual.tracking_view(db, user.id, game_id))


@router.get("/games/{game_id}/achievements")
async def achievements(game_id: UUID, db: Db, user: Owner) -> dict[str, Any]:
    return await _run(db, manual.achievements_view(db, user.id, game_id))


@router.post("/games/{game_id}/manual-achievements", status_code=201)
async def create_achievement(
    game_id: UUID, body: AchievementCreate, db: Db, user: Owner
) -> dict[str, Any]:
    values = body.model_dump(exclude={"scope"})
    row = await _run(
        db,
        manual.create_achievement(db, user.id, game_id, values, umbrella=body.scope == "umbrella"),
    )
    return manual.achievement_out(row)


@router.patch("/manual-achievements/{achievement_id}")
async def update_achievement(
    achievement_id: UUID, body: AchievementUpdate, db: Db, user: Owner
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("title", "x") is None or changes.get("achieved", True) is None:
        raise HTTPException(status_code=422, detail="title and achieved cannot be cleared.")
    row = await _run(db, manual.update_achievement(db, user.id, achievement_id, changes))
    return manual.achievement_out(row)


@router.put("/manual-achievements/{achievement_id}/earned")
async def set_earned(
    achievement_id: UUID, body: EarnedInput, db: Db, user: Owner
) -> dict[str, Any]:
    row = await _run(
        db, manual.set_earned(db, user.id, achievement_id, body.achieved, body.achieved_at)
    )
    return manual.achievement_out(row)


@router.delete("/manual-achievements/{achievement_id}", status_code=204)
async def remove_achievement(achievement_id: UUID, db: Db, user: Owner) -> None:
    await _run(db, manual.remove_achievement(db, user.id, achievement_id))


@router.get("/manual-achievements/{achievement_id}/icon")
async def achievement_icon(
    achievement_id: UUID,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
) -> Response:
    """The icon from this server's own disk, fetched and resized once through the
    same public-host and size checks as provider icons."""
    url = await db.scalar(
        select(GameManualAchievement.icon_url).where(
            GameManualAchievement.id == achievement_id,
            GameManualAchievement.user_id == user.id,
            GameManualAchievement.archived_at.is_(None),
        )
    )
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such icon.")
    target = icon_cache_path(ICON_ROOT, url)
    response = FileResponse(
        target, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"}
    )
    if not target.is_file():
        try:
            await asyncio.to_thread(fetch_and_store, url, target, WIDTHS["icon"])
        except RemoteImageError:
            return original_image_response(url)
    return response


@router.get("/games/{game_id}/completion")
async def completion(game_id: UUID, db: Db, user: Owner) -> dict[str, Any]:
    return await _run(db, manual.completion_view(db, user.id, game_id))


@router.put("/games/{game_id}/completion")
async def set_completion(
    game_id: UUID, body: CompletionInput, db: Db, user: Owner
) -> dict[str, Any]:
    """Set the status of one version by hand; imports and sync leave it alone."""
    await _run(db, manual.set_completion(db, user.id, game_id, body.status))
    return await _run(db, manual.completion_view(db, user.id, game_id))


@router.delete("/games/{game_id}/completion")
async def release_completion(game_id: UUID, db: Db, user: Owner) -> dict[str, Any]:
    """Let imports update this version's status again."""
    await _run(db, manual.release_completion(db, user.id, game_id))
    return await _run(db, manual.completion_view(db, user.id, game_id))


@router.get("/games/{game_id}/completion/history")
async def completion_history(game_id: UUID, db: Db, user: Owner) -> list[dict[str, Any]]:
    return await _run(db, manual.completion_history(db, user.id, game_id))
