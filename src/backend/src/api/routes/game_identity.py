"""Reversible merges, splits and durable same/separate decisions (#95)."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.game_identity import GameIdentityDecision
from src.database.models.user import User
from src.database.session import get_db
from src.features.game_identity import counters, decisions, graph

router = APIRouter(prefix="/api/game-identity", tags=["game"])
_Reason = Annotated[str | None, Field(default=None, max_length=500)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MergeInput(_Body):
    source_id: UUID
    target_id: UUID
    reason: _Reason = None
    replace_separate: bool = False


class UnmergeInput(_Body):
    game_id: UUID
    keep_separate: bool = True
    reason: _Reason = None


class SeparateInput(_Body):
    first_id: UUID
    second_id: UUID
    reason: _Reason = None


class MoveInput(_Body):
    game_id: UUID
    reason: _Reason = None


class RelationshipInput(_Body):
    kind: str = Field(min_length=1, max_length=40)
    from_id: UUID
    to_id: UUID
    label: str = Field(default="", max_length=100)
    display: Literal["default", "link", "nest", "hidden"] = "default"


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    subject_kind: str
    first_game_id: UUID | None
    second_game_id: UUID | None
    observation_id: UUID | None
    outcome: str
    actor_kind: str
    actor_detail: str | None
    reason: str | None
    rule_version: str | None
    confidence: float | None
    evidence: dict
    prior_state: dict | None
    resulting_state: dict | None
    created_at: int
    superseded_at: int | None


class IdentityChange(BaseModel):
    changed: bool
    decision: DecisionOut | None
    detached: list[UUID] = []


class RelationshipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    label: str
    from_game_id: UUID | None
    to_game_id: UUID | None
    display: str
    ended_at: int | None


class EffectiveIdentity(BaseModel):
    game_id: UUID
    identity_id: UUID


def _out(decision: GameIdentityDecision | None) -> DecisionOut | None:
    return None if decision is None else DecisionOut.model_validate(decision)


def _actor(reason: str | None) -> decisions.Actor:
    return decisions.Actor(reason=reason)


async def run_action(db: AsyncSession, action):
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


Db = Annotated[AsyncSession, Depends(get_db)]
Owner = Annotated[User, Depends(get_current_user)]


@router.post("/merge", response_model=IdentityChange)
async def merge(body: MergeInput, db: Db, user: Owner):
    result = await run_action(
        db,
        decisions.merge_identities(
            db,
            user.id,
            body.source_id,
            body.target_id,
            _actor(body.reason),
            replace_separate=body.replace_separate,
        ),
    )
    return IdentityChange(changed=result.merged, decision=_out(result.decision))


@router.post("/unmerge", response_model=IdentityChange)
async def unmerge(body: UnmergeInput, db: Db, user: Owner):
    result = await run_action(
        db,
        decisions.unmerge_identity(
            db, user.id, body.game_id, _actor(body.reason), keep_separate=body.keep_separate
        ),
    )
    return IdentityChange(
        changed=True, decision=_out(result.decision), detached=sorted(result.detached)
    )


@router.post("/separate", response_model=IdentityChange)
async def separate(body: SeparateInput, db: Db, user: Owner):
    decision = await run_action(
        db,
        decisions.separate_identities(
            db, user.id, body.first_id, body.second_id, _actor(body.reason)
        ),
    )
    return IdentityChange(changed=True, decision=_out(decision))


@router.post("/observations/{observation_id}/move", response_model=IdentityChange)
async def move_observation(observation_id: UUID, body: MoveInput, db: Db, user: Owner):
    result = await run_action(
        db,
        decisions.move_observation(db, user.id, observation_id, body.game_id, _actor(body.reason)),
    )
    return IdentityChange(changed=result.link is not None, decision=_out(result.decision))


@router.get("/games/{game_id}", response_model=EffectiveIdentity)
async def effective_identity(game_id: UUID, db: Db, user: Owner):
    identity = await run_action(db, graph.resolve_identity(db, user.id, game_id))
    return EffectiveIdentity(game_id=game_id, identity_id=identity)


@router.get("/decisions", response_model=list[DecisionOut])
async def list_decisions(
    db: Db,
    user: Owner,
    outcome: Annotated[str | None, Query(max_length=30)] = None,
    include_superseded: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    rows = await decisions.list_decisions(
        db, user.id, outcome=outcome, include_superseded=include_superseded, limit=limit
    )
    return [DecisionOut.model_validate(row) for row in rows]


@router.post("/relationships", response_model=RelationshipOut, status_code=201)
async def create_relationship(body: RelationshipInput, db: Db, user: Owner):
    return await run_action(
        db,
        graph.add_relationship(
            db,
            user.id,
            body.kind,
            body.from_id,
            body.to_id,
            label=body.label,
            display=body.display,
        ),
    )


@router.post("/relationships/{relationship_id}/end", response_model=RelationshipOut)
async def end_relationship(relationship_id: UUID, db: Db, user: Owner):
    return await run_action(db, graph.end_relationship(db, user.id, relationship_id))


class AdjustInput(_Body):
    seconds: int = Field(ge=0, le=2_000_000_000)
    counter_id: UUID | None = None
    reason: _Reason = None


class PriorityInput(_Body):
    priority: int = Field(ge=0, le=10_000)


class GroupInput(_Body):
    source_counter_id: UUID


@router.get("/games/{game_id}/metrics")
async def metrics(game_id: UUID, db: Db, user: Owner) -> dict[str, Any]:
    """Effective metrics with raw source values, adjustments and conflict provenance."""
    return await run_action(db, counters.reconcile(db, user.id, game_id))


@router.put("/games/{game_id}/metrics/playtime")
async def adjust_playtime(game_id: UUID, body: AdjustInput, db: Db, user: Owner) -> dict[str, Any]:
    """Set the effective playtime; the correction persists as an offset."""
    await run_action(
        db,
        counters.adjust_total(
            db, user.id, game_id, body.seconds, counter_id=body.counter_id, reason=body.reason
        ),
    )
    return await run_action(db, counters.reconcile(db, user.id, game_id))


@router.delete("/counters/{counter_id}/adjustment", status_code=204)
async def clear_adjustment(counter_id: UUID, db: Db, user: Owner) -> None:
    await run_action(db, counters.clear_adjustment(db, user.id, counter_id))


@router.put("/counters/{counter_id}/members/{observation_id}/priority", status_code=204)
async def set_priority(
    counter_id: UUID, observation_id: UUID, body: PriorityInput, db: Db, user: Owner
) -> None:
    await run_action(
        db, counters.set_priority(db, user.id, counter_id, observation_id, body.priority)
    )


@router.post("/counters/{counter_id}/group")
async def group_counters(
    counter_id: UUID, body: GroupInput, db: Db, user: Owner
) -> dict[str, UUID]:
    """Declare that the source counter reports the same thing as this one."""
    target = await run_action(
        db, counters.group_counters(db, user.id, counter_id, body.source_counter_id)
    )
    return {"counter_id": target}


@router.post("/counters/{counter_id}/members/{observation_id}/ungroup")
async def ungroup(counter_id: UUID, observation_id: UUID, db: Db, user: Owner) -> dict[str, UUID]:
    restored = await run_action(
        db, counters.ungroup_observation(db, user.id, counter_id, observation_id)
    )
    return {"counter_id": restored}
