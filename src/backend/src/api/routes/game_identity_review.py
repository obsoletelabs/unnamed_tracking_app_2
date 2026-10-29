"""Hybrid identity matching, the durable duplicate queue and automatic-merge undo (#99)."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.game_identity import run_action
from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features.game_identity import review

router = APIRouter(prefix="/api/game-identity/review", tags=["game"])
Db = Annotated[AsyncSession, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]


class MergeChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: UUID | None = None
    display: Literal["default", "link", "hidden"] | None = None
    same_copy: bool = False


class RelateChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=40)
    from_id: UUID
    label: str = Field(default="", max_length=100)
    display: Literal["default", "link", "nest", "hidden"] = "default"


class RunOut(BaseModel):
    associated: int
    suggested: int
    skipped: int


async def _act(db: AsyncSession, action) -> Response:
    await run_action(db, action)
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
        db,
        review.resolve_merge(
            db,
            user.id,
            decision_id,
            body.target_id if body else None,
            body.display if body else None,
            body.same_copy if body else False,
        ),
    )


@router.post("/queue/{decision_id}/relate")
async def relate(decision_id: UUID, body: RelateChoice, db: Db, user: Owner):
    return await _act(
        db,
        review.resolve_related(
            db,
            user.id,
            decision_id,
            kind=body.kind,
            from_id=body.from_id,
            label=body.label,
            display=body.display,
        ),
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
