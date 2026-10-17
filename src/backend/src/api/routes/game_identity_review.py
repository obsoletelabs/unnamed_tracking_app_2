"""Hybrid identity matching, the durable duplicate queue and automatic-merge undo (#99)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features.game_identity import graph, review

router = APIRouter(prefix="/api/game-identity/review", tags=["game"])
Db = Annotated[AsyncSession, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]


class MergeChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: UUID | None = None


class RunOut(BaseModel):
    associated: int
    suggested: int
    skipped: int


async def _act(db: AsyncSession, action) -> Response:
    try:
        await action
        await db.commit()
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except graph.IdentityConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except graph.IdentityInvalid as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/run", response_model=RunOut)
async def run(db: Db, user: Owner):
    """Evaluate the library now. Safe to repeat; decided pairs are never reopened."""
    result = await review.run_matching(db, user.id)
    await db.commit()
    return RunOut(associated=result.associated, suggested=result.suggested, skipped=result.skipped)


@router.get("/queue")
async def queue(db: Db, user: Owner, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> Any:
    return await review.queue(db, user.id, limit)


@router.get("/automatic")
async def automatic(db: Db, user: Owner) -> Any:
    return {"merges": await review.automatic_merges(db, user.id)}


@router.post("/queue/{decision_id}/merge")
async def merge(decision_id: UUID, db: Db, user: Owner, body: MergeChoice | None = None):
    return await _act(
        db, review.resolve_merge(db, user.id, decision_id, body.target_id if body else None)
    )


@router.post("/queue/{decision_id}/keep-separate")
async def keep_separate(decision_id: UUID, db: Db, user: Owner):
    return await _act(db, review.resolve_separate(db, user.id, decision_id))


@router.post("/automatic/{decision_id}/undo")
async def undo(decision_id: UUID, db: Db, user: Owner):
    return await _act(db, review.undo_automatic(db, user.id, decision_id))


@router.post("/automatic/{decision_id}/keep")
async def keep(decision_id: UUID, db: Db, user: Owner):
    return await _act(db, review.keep_automatic(db, user.id, decision_id))
