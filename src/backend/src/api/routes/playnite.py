"""Versioned evidence API for the Playnite extension (#100).

Authenticated like every game route (a user `utk_` key or a session). The
extension reports what its library holds; the host decides what that means.
Nothing here creates, merges or splits a canonical identity."""

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import app_grant, get_current_user
from src.core.metrics import DATA_OPERATION_DURATION, DATA_OPERATIONS, DATA_RECORDS
from src.database.models.user import User
from src.database.session import get_db
from src.features.game_ingestion import playnite

router = APIRouter(prefix="/api/playnite", tags=["game"])
Db = Annotated[AsyncSession, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]


def _unsupported(exc: playnite.UnsupportedContract) -> HTTPException:
    """An outdated or newer extension is refused before any data is touched."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error": "unsupported_contract_version",
            "requested": exc.version,
            "supported": list(playnite.SUPPORTED_VERSIONS),
            "message": "Update the Unnamed Tracking Playnite extension or the application.",
        },
    )


@router.get("/contract")
@app_grant("library.sync")
async def contract(_user: Owner) -> dict[str, Any]:
    """Version negotiation and connection test in one authenticated call."""
    return playnite.contract_description()


@router.post("/observations")
@app_grant("library.sync")
async def observations(body: playnite.PlayniteBatchV1, db: Db, user: Owner) -> dict[str, Any]:
    started = time.monotonic()
    try:
        results = await playnite.record_batch(db, user.id, body)
    except playnite.UnsupportedContract as exc:
        DATA_OPERATIONS.labels("playnite", "observations", "rejected_contract").inc()
        raise _unsupported(exc) from exc
    await db.commit()
    DATA_OPERATION_DURATION.labels("playnite", "observations").observe(time.monotonic() - started)
    DATA_OPERATIONS.labels("playnite", "observations", "succeeded").inc()
    for row in results:
        outcome = row.get("outcome")
        if outcome in {"created", "updated", "unchanged", "stale", "unlinked", "in_trash"}:
            DATA_RECORDS.labels("playnite", "game", outcome).inc()
    return {"contract_version": playnite.CURRENT_VERSION, "results": results}


@router.post("/inventory")
@app_grant("library.sync")
async def inventory(body: playnite.PlayniteInventoryV1, db: Db, user: Owner) -> dict[str, Any]:
    """Only after the extension read its whole library without error or cancellation."""
    started = time.monotonic()
    try:
        result = await playnite.record_inventory(db, user.id, body)
    except playnite.UnsupportedContract as exc:
        DATA_OPERATIONS.labels("playnite", "inventory", "rejected_contract").inc()
        raise _unsupported(exc) from exc
    await db.commit()
    DATA_OPERATION_DURATION.labels("playnite", "inventory").observe(time.monotonic() - started)
    DATA_OPERATIONS.labels("playnite", "inventory", "succeeded").inc()
    DATA_RECORDS.labels("playnite", "game", "unavailable").inc(result.marked_unavailable)
    return {
        "contract_version": playnite.CURRENT_VERSION,
        "applied": result.applied,
        "marked_unavailable": result.marked_unavailable,
    }
