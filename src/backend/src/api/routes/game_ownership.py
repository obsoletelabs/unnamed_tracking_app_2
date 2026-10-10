"""Explicit, reversible ownership grouping for any host or plugin game source."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features import game_ownership

router = APIRouter(prefix="/api/game-ownership", tags=["game"])


class OwnedCopyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    main_id: UUID
    copy_id: UUID


@router.post("/group", status_code=204)
async def group_copy(
    body: OwnedCopyInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    try:
        await game_ownership.group_copy(db, user.id, body.main_id, body.copy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/separate", status_code=204)
async def separate_copy(
    body: OwnedCopyInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    try:
        await game_ownership.separate_copy(db, user.id, body.main_id, body.copy_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)
