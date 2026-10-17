"""Name quality, provenance and review (#98): repair placeholders, answer renames."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.game import Game
from src.database.models.game_identity_tracking import FieldValueState, GameFieldValue
from src.database.models.user import User
from src.database.session import get_db
from src.features.game_identity import names

router = APIRouter(prefix="/api/game-identity/names", tags=["game"])
Db = Annotated[AsyncSession, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]


class RepairInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = False


class RepairOut(BaseModel):
    checked: int
    tracked: int
    replaced: list[UUID]
    proposed: list[UUID]
    unresolved: list[UUID]
    dry_run: bool


class NameReview(BaseModel):
    id: UUID
    game_id: UUID
    current_title: str
    proposed_title: str
    origin: str
    quality: str
    observation_id: UUID | None
    recorded_at: int


class NameProvenance(BaseModel):
    game_id: UUID
    title: str
    quality: str
    origin: str | None
    locked: bool
    lock_origin: str | None
    source_names: list[dict[str, Any]]
    history: list[dict[str, Any]]


def _review(value: GameFieldValue, game: Game) -> NameReview:
    return NameReview(
        id=value.id,
        game_id=game.id,
        current_title=game.title,
        proposed_title=str(value.value),
        origin=value.origin,
        quality=value.quality,
        observation_id=value.observation_id,
        recorded_at=value.recorded_at,
    )


@router.post("/repair", response_model=RepairOut)
async def repair(body: RepairInput, db: Db, user: Owner) -> RepairOut:
    """Record name provenance for existing games and upgrade recognised placeholders from
    names their sources already reported. Never creates or deletes a game."""
    report = await names.repair_games(db, user.id, dry_run=body.dry_run)
    if body.dry_run:
        await db.rollback()
    else:
        await db.commit()
    return RepairOut(**vars(report), dry_run=body.dry_run)


@router.get("/reviews", response_model=list[NameReview])
async def reviews(db: Db, user: Owner) -> list[NameReview]:
    return [_review(value, game) for value, game in await names.pending_reviews(db, user.id)]


async def _answer(db: AsyncSession, action) -> dict[str, Any]:
    try:
        game = await action
        await db.commit()
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"game_id": game.id, "title": game.title}


@router.post("/reviews/{review_id}/update")
async def update_name(review_id: UUID, db: Db, user: Owner) -> dict[str, Any]:
    """Update: take the name the source reported."""
    return await _answer(db, names.accept_review(db, user.id, review_id))


@router.post("/reviews/{review_id}/keep")
async def keep_name(review_id: UUID, db: Db, user: Owner) -> dict[str, Any]:
    """Keep current: the same source name is not offered again."""
    return await _answer(db, names.keep_review(db, user.id, review_id))


@router.get("/games/{game_id}", response_model=NameProvenance)
async def provenance(game_id: UUID, db: Db, user: Owner) -> NameProvenance:
    game = await db.scalar(select(Game).where(Game.id == game_id, Game.user_id == user.id))
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    values = (
        await db.scalars(
            select(GameFieldValue)
            .where(GameFieldValue.user_id == user.id, GameFieldValue.game_id == game_id)
            .where(GameFieldValue.field == names.TITLE)
            .order_by(GameFieldValue.recorded_at, GameFieldValue.id)
        )
    ).all()
    current = next((v for v in values if v.state == FieldValueState.CURRENT.value), None)
    observations = await names.linked_observations(db, user.id, game_id)
    return NameProvenance(
        game_id=game.id,
        title=game.title,
        quality=current.quality
        if current
        else names.classify_title(
            game.title, source=game.source, external_id=game.external_id
        ).value,
        origin=current.origin if current else None,
        locked="title" in (game.locked_fields or []),
        lock_origin=current.lock_origin if current else None,
        source_names=[
            {"observation_id": str(o.id), "external_id": o.external_id, "title": o.observed_title}
            for o in observations
        ],
        history=[
            {
                "value": v.value,
                "state": v.state,
                "origin": v.origin,
                "quality": v.quality,
                "observation_id": str(v.observation_id) if v.observation_id else None,
                "recorded_at": v.recorded_at,
            }
            for v in values
        ],
    )
