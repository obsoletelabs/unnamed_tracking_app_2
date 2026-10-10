"""User-scoped duplicate review without provider-specific or notification coupling."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.game import GameStatus
from src.database.models.user import User
from src.database.session import get_db
from src.features import game_duplicates

router = APIRouter(prefix="/api/game-duplicates", tags=["game"])


class DuplicateGame(BaseModel):
    id: UUID
    title: str
    source: str | None
    platform: str | None
    release_date: date | None
    external_id: str | None
    provider_ids: dict[str, str]
    status: GameStatus
    playtime_seconds: int
    locked_fields: list[str]


class DuplicatePair(BaseModel):
    first: DuplicateGame
    second: DuplicateGame
    reason: Literal["same_title", "shared_identity"]


class DuplicateSuggestions(BaseModel):
    pairs: list[DuplicatePair]
    has_more: bool


class KeepBothInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_id: UUID
    second_id: UUID


@router.get("", response_model=DuplicateSuggestions)
async def list_duplicates(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return await game_duplicates.suggestions(db, user.id, limit)


@router.post("/keep-both", status_code=204)
async def keep_both(
    body: KeepBothInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    try:
        await game_duplicates.keep_both(db, user.id, body.first_id, body.second_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)
