"""Status and trigger of the library backfill into the identity model (#102)."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features.game_identity_backfill import runner

router = APIRouter(prefix="/api/game-identity/backfill", tags=["game"])
Db = Annotated[AsyncSession, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]
_TASKS: set[asyncio.Task[Any]] = set()


@router.get("")
async def backfill_status(db: Db, user: Owner) -> dict[str, Any]:
    """Progress, per-stage counts and error codes of the signed-in user's backfill."""
    return await runner.status(db, user.id)


@router.get("/plan")
async def backfill_plan(db: Db, user: Owner) -> dict[str, int]:
    """What a run would still do; writes nothing."""
    return await runner.plan(db, user.id)


@router.post("", status_code=202)
async def start_backfill(user: Owner, wait: bool = False) -> dict[str, Any]:
    """Start (or resume) the backfill for the signed-in user. Safe to repeat.

    Runs in the background unless `wait=true`; poll `GET` for progress."""
    if wait:
        try:
            return await runner.run_user(user.id)
        except runner.BackfillBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    task = asyncio.create_task(_background(user.id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"status": "started"}


async def _background(user_id: Any) -> None:
    try:
        await runner.run_user(user_id)
    except runner.BackfillBusy:
        return
