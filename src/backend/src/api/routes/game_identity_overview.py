"""Library layout, umbrella overview and identity management reads/edits (#101)."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features.game_identity import graph, overview

router = APIRouter(prefix="/api/game-identity", tags=["game"])
Db = Annotated[AsyncSession, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]
Display = Literal["default", "link", "nest", "hidden"]


class DisplayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display: Display


class RelationshipChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=100)
    display: Display | None = None


async def _run(db: AsyncSession, action: Any) -> Any:
    try:
        result = await action
        await db.commit()
        return result
    except LookupError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except graph.IdentityConflict as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except graph.IdentityInvalid as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _edge(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "label": row.label,
        "from_id": row.from_game_id,
        "to_id": row.to_game_id,
        "display": row.display,
        "ended_at": row.ended_at,
    }


@router.get("/library")
async def library(db: Db, user: Owner) -> dict[str, Any]:
    return await overview.library_layout(db, user.id)


@router.get("/games/{game_id}/overview")
async def game_overview(game_id: UUID, db: Db, user: Owner) -> dict[str, Any]:
    return await _run(db, overview.game_overview(db, user.id, game_id))


@router.get("/games/{game_id}/sources")
async def game_sources(game_id: UUID, db: Db, user: Owner) -> dict[str, Any]:
    return await _run(db, overview.identity_sources(db, user.id, game_id))


@router.put("/relationships/{relationship_id}/display")
async def relationship_display(
    relationship_id: UUID, body: DisplayInput, db: Db, user: Owner
) -> dict[str, Any]:
    row = await _run(
        db, overview.set_relationship_display(db, user.id, relationship_id, body.display)
    )
    return _edge(row)


@router.put("/relationships/{relationship_id}")
async def relationship_change(
    relationship_id: UUID, body: RelationshipChange, db: Db, user: Owner
) -> dict[str, Any]:
    row = await _run(
        db,
        overview.change_relationship(
            db, user.id, relationship_id, kind=body.kind, label=body.label, display=body.display
        ),
    )
    return _edge(row)
