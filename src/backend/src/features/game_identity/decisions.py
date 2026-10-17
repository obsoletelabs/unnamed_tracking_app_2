"""Durable same/separate decisions, reversible merges and observation moves.

Every change takes the per-user identity lock, so simultaneous syncs, user edits
and automatic matching are applied one after another. Callers commit."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game_identity import (
    DecisionActor,
    DecisionOutcome,
    DecisionSubject,
    GameIdentityDecision,
    GameRelationship,
    GameSourceObservation,
    GameSourceObservationLink,
    RecordOrigin,
    RelationshipKind,
)
from src.features.game_identity import counters
from src.features.game_identity.graph import (
    IdentityConflict,
    IdentityInvalid,
    MergeGraph,
    lock_user_identities,
    merge_graph,
    now_seconds,
    require_games,
)
from src.features.game_ingestion.service import relink_observation

_CONFIRMING = frozenset({DecisionActor.USER.value, DecisionActor.MIGRATION.value})
_SAME = (DecisionOutcome.CONFIRMED_SAME.value, DecisionOutcome.AUTOMATIC_SAME.value)
_CONFIRMED_SAME = DecisionOutcome.CONFIRMED_SAME.value
_SEPARATE = DecisionOutcome.CONFIRMED_SEPARATE.value
_UNRESOLVED = DecisionOutcome.UNRESOLVED.value
_SAME_GAME = RelationshipKind.SAME_GAME.value
_IDENTITY_PAIR = DecisionSubject.IDENTITY_PAIR.value
_OBSERVATION = DecisionSubject.OBSERVATION_IDENTITY.value
_SUBJECT_COLUMNS = (
    "first_game_id",
    "second_game_id",
    "observation_id",
    "first_umbrella_id",
    "second_umbrella_id",
)


@dataclass(frozen=True)
class Actor:
    """Who decided. `kind` is user, migration or automatic; `detail` is a rule or
    credential name, never a secret."""

    kind: str = DecisionActor.USER.value
    detail: str | None = None
    reason: str | None = None

    @property
    def confirms(self) -> bool:
        return self.kind in _CONFIRMING

    @property
    def origin(self) -> str:
        if self.kind == DecisionActor.USER.value:
            return RecordOrigin.USER.value
        if self.kind == DecisionActor.MIGRATION.value:
            return RecordOrigin.MIGRATION.value
        return RecordOrigin.AUTOMATIC.value


@dataclass
class MergeResult:
    merged: bool
    decision: GameIdentityDecision
    relationship: GameRelationship | None
    superseded_separate: list[UUID] = field(default_factory=list)


@dataclass
class SplitResult:
    decision: GameIdentityDecision | None
    former_parent: UUID
    detached: set[UUID]


@dataclass
class MoveResult:
    link: GameSourceObservationLink | None
    decision: GameIdentityDecision
    previous_game_id: UUID | None


@dataclass
class AutomaticResult:
    outcome: str  # associated | suggested | unchanged | blocked
    decision: GameIdentityDecision | None = None


def _pair(first: UUID, second: UUID) -> dict[str, Any]:
    low, high = (first, second) if first < second else (second, first)
    return {"first_game_id": low, "second_game_id": high}


def _subject_filter(subject: dict[str, Any]) -> list[Any]:
    return [
        getattr(GameIdentityDecision, name) == subject[name]
        if subject.get(name) is not None
        else getattr(GameIdentityDecision, name).is_(None)
        for name in _SUBJECT_COLUMNS
    ]


async def _current(
    db: AsyncSession, user_id: UUID, subject_kind: str, subject: dict[str, Any], *, lock: bool
) -> GameIdentityDecision | None:
    statement = select(GameIdentityDecision).where(
        GameIdentityDecision.user_id == user_id,
        GameIdentityDecision.subject_kind == subject_kind,
        GameIdentityDecision.superseded_at.is_(None),
        *_subject_filter(subject),
    )
    return await db.scalar(statement.with_for_update() if lock else statement)


async def write_decision(
    db: AsyncSession,
    user_id: UUID,
    subject_kind: str,
    subject: dict[str, Any],
    outcome: str,
    actor: Actor,
    *,
    rule_version: str | None = None,
    confidence: float | None = None,
    evidence: dict[str, Any] | None = None,
    resulting_state: dict[str, Any] | None = None,
    prior_state: dict[str, Any] | None = None,
    now: int | None = None,
) -> GameIdentityDecision:
    """Supersede the current decision for the subject and append the new one.

    A repeat of the same outcome by the same kind of actor changes nothing. An
    automatic or source actor can never replace a user's confirmed decision."""
    stamp = now_seconds() if now is None else now
    current = await _current(db, user_id, subject_kind, subject, lock=True)
    if current is not None:
        if (current.outcome, current.actor_kind) == (outcome, actor.kind):
            return current
        if current.outcome in (_SEPARATE, _CONFIRMED_SAME) and not actor.confirms:
            raise IdentityConflict("A user decision outranks automatic matching")
        current.superseded_at = max(stamp, current.created_at)
        await db.flush()
    if prior_state is None and current is not None:
        prior_state = {
            "decision_id": str(current.id),
            "outcome": current.outcome,
            "actor_kind": current.actor_kind,
        }
    decision = GameIdentityDecision(
        user_id=user_id,
        subject_kind=subject_kind,
        outcome=outcome,
        actor_kind=actor.kind,
        actor_detail=actor.detail,
        reason=actor.reason,
        rule_version=rule_version,
        confidence=None if confidence is None else Decimal(str(confidence)),
        evidence=evidence or {},
        prior_state=prior_state,
        resulting_state=resulting_state,
        created_at=stamp,
        **{key: value for key, value in subject.items() if value is not None},
    )
    db.add(decision)
    await db.flush()
    return decision


async def _separate_between(
    db: AsyncSession, user_id: UUID, left: set[UUID], right: set[UUID]
) -> list[GameIdentityDecision]:
    """Current `confirmed_separate` pair decisions joining the two groups."""
    return list(
        await db.scalars(
            select(GameIdentityDecision).where(
                GameIdentityDecision.user_id == user_id,
                GameIdentityDecision.subject_kind == _IDENTITY_PAIR,
                GameIdentityDecision.outcome == _SEPARATE,
                GameIdentityDecision.superseded_at.is_(None),
                or_(
                    and_(
                        GameIdentityDecision.first_game_id.in_(left),
                        GameIdentityDecision.second_game_id.in_(right),
                    ),
                    and_(
                        GameIdentityDecision.first_game_id.in_(right),
                        GameIdentityDecision.second_game_id.in_(left),
                    ),
                ),
            )
        )
    )


def _require_confirming(actor: Actor) -> None:
    if not actor.confirms:
        raise IdentityInvalid("Only a user (or a migration of a user choice) can decide this")


async def _attach(
    db: AsyncSession,
    user_id: UUID,
    graph: MergeGraph,
    source_id: UUID,
    target_id: UUID,
    decision: GameIdentityDecision,
    actor: Actor,
    now: int,
) -> GameRelationship:
    """Absorb the source's group root into the target's group root."""
    absorbed, root = graph.root(source_id), graph.root(target_id)
    relationship = GameRelationship(
        user_id=user_id,
        kind=_SAME_GAME,
        label="",
        from_game_id=absorbed,
        to_game_id=root,
        origin=actor.origin,
        decision_id=decision.id,
        created_at=now,
        updated_at=now,
    )
    db.add(relationship)
    await db.flush()
    decision.resulting_state = {
        "relationship_id": str(relationship.id),
        "absorbed": str(absorbed),
        "into": str(root),
    }
    return relationship


async def merge_identities(
    db: AsyncSession,
    user_id: UUID,
    source_id: UUID,
    target_id: UUID,
    actor: Actor | None = None,
    *,
    replace_separate: bool = False,
    now: int | None = None,
) -> MergeResult:
    """Record that two tracked identities are the same game.

    Creates a `same_game` relationship between the two groups; no game,
    observation, counter or history row changes. Repeating it is a no-op. A
    standing 'keep separate' decision blocks the merge unless
    `replace_separate` says the user is reversing it."""
    actor = actor or Actor()
    _require_confirming(actor)
    if source_id == target_id:
        raise IdentityInvalid("An identity cannot be merged with itself")
    stamp = now_seconds() if now is None else now
    await lock_user_identities(db, user_id)
    await require_games(db, user_id, source_id, target_id)
    graph = await merge_graph(db, user_id)
    pair = _pair(source_id, target_id)
    if graph.root(source_id) == graph.root(target_id):
        decision = await write_decision(
            db, user_id, _IDENTITY_PAIR, pair, _CONFIRMED_SAME, actor, now=stamp
        )
        edge = graph.edge.get(source_id)
        if edge is not None and graph.parent[source_id] == target_id:
            edge.origin, edge.decision_id = actor.origin, decision.id  # user outranks automatic
        return MergeResult(False, decision, edge)
    left, right = graph.group(source_id), graph.group(target_id)
    blocking = await _separate_between(db, user_id, left, right)
    if blocking and not replace_separate:
        raise IdentityConflict("These games were marked as separate; confirm to reverse that")
    for old in blocking:
        old.superseded_at = max(stamp, old.created_at)
    await db.flush()
    decision = await write_decision(
        db,
        user_id,
        _IDENTITY_PAIR,
        pair,
        _CONFIRMED_SAME,
        actor,
        evidence={"groups": [sorted(map(str, left)), sorted(map(str, right))]},
        now=stamp,
    )
    relationship = await _attach(db, user_id, graph, source_id, target_id, decision, actor, stamp)
    return MergeResult(True, decision, relationship, [old.id for old in blocking])


async def _split(
    db: AsyncSession,
    user_id: UUID,
    graph: MergeGraph,
    game_id: UUID,
    stamp: int,
    decided: dict[str, Any],
) -> SplitResult:
    """End the edge that attaches `game_id` (and what merged into it) to its parent."""
    edge, parent = graph.edge.get(game_id), graph.parent.get(game_id)
    if edge is None or parent is None:
        raise IdentityConflict("This game is not merged into another game")
    detached = graph.subtree(game_id)
    rest = graph.group(game_id) - detached
    edge.ended_at = max(stamp, edge.created_at)
    crossing = list(
        await db.scalars(
            select(GameIdentityDecision).where(
                GameIdentityDecision.user_id == user_id,
                GameIdentityDecision.subject_kind == _IDENTITY_PAIR,
                GameIdentityDecision.outcome.in_(_SAME),
                GameIdentityDecision.superseded_at.is_(None),
                or_(
                    and_(
                        GameIdentityDecision.first_game_id.in_(detached),
                        GameIdentityDecision.second_game_id.in_(rest),
                    ),
                    and_(
                        GameIdentityDecision.first_game_id.in_(rest),
                        GameIdentityDecision.second_game_id.in_(detached),
                    ),
                ),
            )
        )
    )
    await db.flush()
    for old in crossing:
        if (old.first_game_id, old.second_game_id) == (
            decided["first_game_id"],
            decided["second_game_id"],
        ):
            continue  # the caller supersedes this one with the explicit outcome
        pair = {"first_game_id": old.first_game_id, "second_game_id": old.second_game_id}
        await write_decision(
            db,
            user_id,
            _IDENTITY_PAIR,
            pair,
            _UNRESOLVED,
            Actor(DecisionActor.USER.value, reason="The group was split"),
            now=stamp,
        )
    return SplitResult(None, parent, detached)


async def unmerge_identity(
    db: AsyncSession,
    user_id: UUID,
    game_id: UUID,
    actor: Actor | None = None,
    *,
    keep_separate: bool = True,
    now: int | None = None,
) -> SplitResult:
    """Split a game (with whatever was merged into it) back out of its group.

    The merge edge is ended and the pair decision superseded, so the original
    observations and adjustments give each side its independent view again.
    `keep_separate` also persists 'these are different games' so automatic
    matching never merges them again."""
    actor = actor or Actor()
    _require_confirming(actor)
    stamp = now_seconds() if now is None else now
    await lock_user_identities(db, user_id)
    await require_games(db, user_id, game_id)
    graph = await merge_graph(db, user_id)
    parent = graph.parent.get(game_id)
    decided = _pair(game_id, parent) if parent else {"first_game_id": None, "second_game_id": None}
    result = await _split(db, user_id, graph, game_id, stamp, decided)
    result.decision = await write_decision(
        db,
        user_id,
        _IDENTITY_PAIR,
        _pair(game_id, result.former_parent),
        _SEPARATE if keep_separate else _UNRESOLVED,
        actor,
        resulting_state={"detached": sorted(map(str, result.detached))},
        now=stamp,
    )
    return result


async def separate_identities(
    db: AsyncSession,
    user_id: UUID,
    first_id: UUID,
    second_id: UUID,
    actor: Actor | None = None,
    *,
    now: int | None = None,
) -> GameIdentityDecision:
    """Persist 'these are different games', splitting them if currently merged."""
    actor = actor or Actor()
    _require_confirming(actor)
    if first_id == second_id:
        raise IdentityInvalid("A game cannot be separate from itself")
    stamp = now_seconds() if now is None else now
    await lock_user_identities(db, user_id)
    await require_games(db, user_id, first_id, second_id)
    graph = await merge_graph(db, user_id)
    if graph.root(first_id) == graph.root(second_id):
        # detach the one that is not the other's ancestor
        child = second_id if second_id in graph.subtree(first_id) else first_id
        await _split(db, user_id, graph, child, stamp, _pair(first_id, second_id))
    return await write_decision(
        db, user_id, _IDENTITY_PAIR, _pair(first_id, second_id), _SEPARATE, actor, now=stamp
    )


async def is_pair_rejected(
    db: AsyncSession, user_id: UUID, first_id: UUID, second_id: UUID
) -> bool:
    """True when the user said the two groups are separate (matchers must skip)."""
    graph = await merge_graph(db, user_id)
    left, right = graph.group(first_id), graph.group(second_id)
    return bool(await _separate_between(db, user_id, left, right))


async def associate_automatically(
    db: AsyncSession,
    user_id: UUID,
    first_id: UUID,
    second_id: UUID,
    *,
    rule: str,
    rule_version: str,
    confidence: float,
    reason: str,
    evidence: dict[str, Any] | None = None,
    merge: bool = True,
    now: int | None = None,
) -> AutomaticResult:
    """An automatic match proposes (`merge=False`) or applies a same-game link.

    Never overrides a user decision: a pair whose groups hold a rejected pair is
    `blocked`; a pair already decided is `unchanged`. A new third record is
    compared on its own, so an earlier rejection never hides or reapplies."""
    if first_id == second_id:
        raise IdentityInvalid("An identity cannot be matched with itself")
    actor = Actor(DecisionActor.AUTOMATIC.value, rule, reason)
    stamp = now_seconds() if now is None else now
    await lock_user_identities(db, user_id)
    await require_games(db, user_id, first_id, second_id)
    graph = await merge_graph(db, user_id)
    if graph.root(first_id) == graph.root(second_id):
        return AutomaticResult("unchanged")
    if await _separate_between(db, user_id, graph.group(first_id), graph.group(second_id)):
        return AutomaticResult("blocked")
    existing = await _current(db, user_id, _IDENTITY_PAIR, _pair(first_id, second_id), lock=False)
    if existing is not None and (
        existing.outcome != _UNRESOLVED or (not merge and existing.rule_version == rule_version)
    ):
        return AutomaticResult("unchanged", existing)
    outcome = DecisionOutcome.AUTOMATIC_SAME if merge else DecisionOutcome.UNRESOLVED
    decision = await write_decision(
        db,
        user_id,
        _IDENTITY_PAIR,
        _pair(first_id, second_id),
        outcome.value,
        actor,
        rule_version=rule_version,
        confidence=confidence,
        evidence=evidence,
        now=stamp,
    )
    if not merge:
        return AutomaticResult("suggested", decision)
    await _attach(db, user_id, graph, first_id, second_id, decision, actor, stamp)
    return AutomaticResult("associated", decision)


async def move_observation(
    db: AsyncSession,
    user_id: UUID,
    observation_id: UUID,
    game_id: UUID,
    actor: Actor | None = None,
    *,
    now: int | None = None,
) -> MoveResult:
    """Attach a source record to another tracked identity by a user decision.

    The record keeps its source ID and revisions; the old link is ended (history),
    and the old pairing is persisted as rejected so a matcher cannot move it back."""
    actor = actor or Actor()
    _require_confirming(actor)
    stamp = now_seconds() if now is None else now
    await lock_user_identities(db, user_id)
    await require_games(db, user_id, game_id)
    if not await db.scalar(
        select(GameSourceObservation.id).where(
            GameSourceObservation.id == observation_id, GameSourceObservation.user_id == user_id
        )
    ):
        raise LookupError("Observation not found")
    previous = await db.scalar(
        select(GameSourceObservationLink.game_id).where(
            GameSourceObservationLink.observation_id == observation_id,
            GameSourceObservationLink.unlinked_at.is_(None),
        )
    )
    decision = await write_decision(
        db,
        user_id,
        _OBSERVATION,
        {"observation_id": observation_id, "second_game_id": game_id},
        _CONFIRMED_SAME,
        actor,
        prior_state={"game_id": str(previous) if previous else None},
        resulting_state={"game_id": str(game_id)},
        now=stamp,
    )
    if previous is not None and previous != game_id:
        await write_decision(
            db,
            user_id,
            _OBSERVATION,
            {"observation_id": observation_id, "second_game_id": previous},
            _SEPARATE,
            Actor(actor.kind, actor.detail, "Moved to another game"),
            now=stamp,
        )
    link = await relink_observation(
        db,
        user_id,
        observation_id,
        game_id,
        origin=actor.origin,
        decision_id=decision.id,
        now=stamp,
    )
    if link is not None:
        await counters.follow_observation(db, user_id, observation_id, now=stamp)
    return MoveResult(link, decision, previous)


async def observation_match_rejected(
    db: AsyncSession, user_id: UUID, observation_id: UUID, game_id: UUID
) -> bool:
    current = await _current(
        db,
        user_id,
        _OBSERVATION,
        {"observation_id": observation_id, "second_game_id": game_id},
        lock=False,
    )
    return current is not None and current.outcome == _SEPARATE


async def list_decisions(
    db: AsyncSession,
    user_id: UUID,
    *,
    outcome: str | None = None,
    include_superseded: bool = False,
    limit: int = 100,
) -> list[GameIdentityDecision]:
    statement = select(GameIdentityDecision).where(GameIdentityDecision.user_id == user_id)
    if not include_superseded:
        statement = statement.where(GameIdentityDecision.superseded_at.is_(None))
    if outcome is not None:
        statement = statement.where(GameIdentityDecision.outcome == outcome)
    ordered = statement.order_by(GameIdentityDecision.created_at.desc(), GameIdentityDecision.id)
    return list(await db.scalars(ordered.limit(limit)))
