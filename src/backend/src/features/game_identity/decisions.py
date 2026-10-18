"""Durable same/separate decisions, reversible merges and observation moves.

Every change takes the per-user identity lock, so simultaneous syncs, user edits
and automatic matching are applied one after another. Callers commit."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_identity import (
    DecisionActor,
    DecisionOutcome,
    DecisionSubject,
    GameIdentity,
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

logger = logging.getLogger(__name__)

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
    relationship: GameRelationship | None = None  # the new same-game edge when associated


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
    *,
    outcome: str,
    actor: Actor,
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
    # Provenance for operators: who decided what, never a title, a source ID or a credential.
    logger.info(
        "identity decision user=%s decision=%s subject=%s outcome=%s actor=%s rule=%s superseded=%s",
        user_id,
        decision.id,
        subject_kind,
        outcome,
        actor.kind,
        rule_version,
        None if current is None else current.id,
    )
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
    *,
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


async def _confirm_existing(
    db: AsyncSession,
    user_id: UUID,
    graph: MergeGraph,
    pair_ids: tuple[UUID, UUID],
    *,
    actor: Actor,
    stamp: int,
) -> MergeResult:
    """Record a user's confirmation of games that are already in one group."""
    source_id, target_id = pair_ids
    decision = await write_decision(
        db,
        user_id,
        _IDENTITY_PAIR,
        _pair(source_id, target_id),
        outcome=_CONFIRMED_SAME,
        actor=actor,
        now=stamp,
    )
    edge = graph.edge.get(source_id)
    if edge is not None and graph.parent[source_id] == target_id:
        edge.origin, edge.decision_id = actor.origin, decision.id  # user outranks automatic
    return MergeResult(False, decision, edge)


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
    if graph.root(source_id) == graph.root(target_id):
        return await _confirm_existing(
            db, user_id, graph, (source_id, target_id), actor=actor, stamp=stamp
        )
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
        _pair(source_id, target_id),
        outcome=_CONFIRMED_SAME,
        actor=actor,
        evidence={"groups": [sorted(map(str, left)), sorted(map(str, right))]},
        now=stamp,
    )
    relationship = await _attach(
        db, user_id, graph, source_id, target_id, decision=decision, actor=actor, now=stamp
    )
    return MergeResult(True, decision, relationship, [old.id for old in blocking])


async def _split(
    db: AsyncSession,
    user_id: UUID,
    graph: MergeGraph,
    game_id: UUID,
    *,
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
            outcome=_UNRESOLVED,
            actor=Actor(DecisionActor.USER.value, reason="The group was split"),
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
    result = await _split(db, user_id, graph, game_id, stamp=stamp, decided=decided)
    result.decision = await write_decision(
        db,
        user_id,
        _IDENTITY_PAIR,
        _pair(game_id, result.former_parent),
        outcome=_SEPARATE if keep_separate else _UNRESOLVED,
        actor=actor,
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
        await _split(db, user_id, graph, child, stamp=stamp, decided=_pair(first_id, second_id))
    return await write_decision(
        db,
        user_id,
        _IDENTITY_PAIR,
        _pair(first_id, second_id),
        outcome=_SEPARATE,
        actor=actor,
        now=stamp,
    )


async def is_pair_rejected(
    db: AsyncSession, user_id: UUID, first_id: UUID, second_id: UUID
) -> bool:
    """True when the user said the two groups are separate (matchers must skip)."""
    graph = await merge_graph(db, user_id)
    left, right = graph.group(first_id), graph.group(second_id)
    return bool(await _separate_between(db, user_id, left, right))


async def _settled_automatic(
    db: AsyncSession,
    user_id: UUID,
    graph: MergeGraph,
    pair_ids: tuple[UUID, UUID],
    *,
    merge: bool,
    rule_version: str,
) -> AutomaticResult | None:
    """The result that makes a new automatic match a no-op or blocked, if any."""
    first_id, second_id = pair_ids
    if graph.root(first_id) == graph.root(second_id):
        return AutomaticResult("unchanged")
    if await _separate_between(db, user_id, graph.group(first_id), graph.group(second_id)):
        return AutomaticResult("blocked")
    existing = await _current(db, user_id, _IDENTITY_PAIR, _pair(first_id, second_id), lock=False)
    if existing is not None and (
        existing.outcome != _UNRESOLVED or (not merge and existing.rule_version == rule_version)
    ):
        return AutomaticResult("unchanged", existing)
    return None


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
    graph: MergeGraph | None = None,
) -> AutomaticResult:
    """An automatic match proposes (`merge=False`) or applies a same-game link.

    Never overrides a user decision: a pair whose groups hold a rejected pair is
    `blocked`; a pair already decided is `unchanged`. A new third record is
    compared on its own, so an earlier rejection never hides or reapplies.

    A caller evaluating many pairs in one transaction (the matcher) passes the merge graph
    it already holds and keeps it current, instead of re-reading every edge for each pair. By
    passing it, the caller also states that it already holds the user's identity lock and has
    read both games from that user's own library."""
    if first_id == second_id:
        raise IdentityInvalid("An identity cannot be matched with itself")
    actor = Actor(DecisionActor.AUTOMATIC.value, rule, reason)
    now = now_seconds() if now is None else now
    if graph is None:
        await lock_user_identities(db, user_id)
        await require_games(db, user_id, first_id, second_id)
        graph = await merge_graph(db, user_id)
    settled = await _settled_automatic(
        db, user_id, graph, (first_id, second_id), merge=merge, rule_version=rule_version
    )
    if settled is not None:
        return settled
    decision = await write_decision(
        db,
        user_id,
        _IDENTITY_PAIR,
        _pair(first_id, second_id),
        outcome=(DecisionOutcome.AUTOMATIC_SAME if merge else DecisionOutcome.UNRESOLVED).value,
        actor=actor,
        rule_version=rule_version,
        confidence=confidence,
        evidence=evidence,
        now=now,
    )
    if not merge:
        return AutomaticResult("suggested", decision)
    return AutomaticResult(
        "associated",
        decision,
        await _attach(
            db, user_id, graph, first_id, second_id, decision=decision, actor=actor, now=now
        ),
    )


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
        outcome=_CONFIRMED_SAME,
        actor=actor,
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
            outcome=_SEPARATE,
            actor=Actor(actor.kind, actor.detail, "Moved to another game"),
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


@dataclass
class MainResult:
    main_id: UUID
    previous_main_id: UUID
    changed: bool


async def _rebuild_star(
    db: AsyncSession,
    user_id: UUID,
    graph: MergeGraph,
    group: set[UUID],
    main_id: UUID,
    *,
    origin: str,
    stamp: int,
) -> None:
    """Attach every member of `group` directly to `main_id`.

    Edges are ended, never deleted. Each copy keeps the display choice and the
    decision that brought it into the group; the former main, which had no
    edge, takes the decision the new main joined with."""
    previous_root = graph.root(main_id)
    joined_with = graph.edge.get(main_id)
    for member in group:
        edge = graph.edge.get(member)
        if member == main_id or (edge is not None and graph.parent[member] == main_id):
            continue
        display = edge.display if edge is not None else "default"
        decision_id = edge.decision_id if edge is not None else None
        if member == previous_root and joined_with is not None:
            decision_id = joined_with.decision_id
        if edge is not None:
            edge.ended_at = max(stamp, edge.created_at)
        db.add(
            GameRelationship(
                user_id=user_id,
                kind=_SAME_GAME,
                label="",
                from_game_id=member,
                to_game_id=main_id,
                display=display,
                origin=origin,
                decision_id=decision_id,
                created_at=stamp,
                updated_at=stamp,
            )
        )
    if joined_with is not None:
        joined_with.ended_at = max(stamp, joined_with.created_at)
    # End the old edges before the new ones exist: a game has one active target.
    pending = [row for row in db.new if isinstance(row, GameRelationship)]
    for row in pending:
        db.expunge(row)
    await db.flush()
    db.add_all(pending)
    await db.flush()


async def _mark_primary(db: AsyncSession, user_id: UUID, group: set[UUID], main_id: UUID) -> None:
    """Keep the umbrella's primary flag on the group's main copy."""
    placements = list(
        await db.scalars(
            select(GameIdentity).where(
                GameIdentity.user_id == user_id, GameIdentity.game_id.in_(group)
            )
        )
    )
    for row in placements:
        row.is_primary = False
    await db.flush()
    for row in placements:
        if row.game_id == main_id:
            row.is_primary = True
    await db.flush()


async def make_main(
    db: AsyncSession,
    user_id: UUID,
    game_id: UUID,
    actor: Actor | None = None,
    *,
    now: int | None = None,
) -> MainResult:
    """Make one copy the main entry of its group (its title, cover and page).

    Membership, observations, counters and adjustments do not change; only which
    copy the others are attached to. Repeating it is a no-op."""
    actor = actor or Actor()
    _require_confirming(actor)
    stamp = now_seconds() if now is None else now
    await lock_user_identities(db, user_id)
    await require_games(db, user_id, game_id)
    graph = await merge_graph(db, user_id)
    previous = graph.root(game_id)
    group = graph.group(game_id)
    flat = all(graph.parent.get(m) == game_id for m in group - {game_id})
    if previous == game_id and flat:
        return MainResult(game_id, previous, False)
    await _rebuild_star(db, user_id, graph, group, game_id, origin=actor.origin, stamp=stamp)
    await _mark_primary(db, user_id, group, game_id)
    return MainResult(game_id, previous, previous != game_id)


async def split_copy(
    db: AsyncSession,
    user_id: UUID,
    game_id: UUID,
    actor: Actor | None = None,
    *,
    keep_separate: bool = True,
    now: int | None = None,
) -> SplitResult:
    """Take exactly one copy out of its group.

    `unmerge_identity` detaches a game together with whatever was merged into
    it. Users splitting one copy expect only that copy to leave, so the group is
    first flattened around its main copy. Splitting the main copy promotes the
    next copy (most playtime first) to main."""
    actor = actor or Actor()
    _require_confirming(actor)
    stamp = now_seconds() if now is None else now
    await lock_user_identities(db, user_id)
    await require_games(db, user_id, game_id)
    graph = await merge_graph(db, user_id)
    group = graph.group(game_id)
    if len(group) < 2:
        raise IdentityConflict("This game is not merged into another game")
    main_id = graph.root(game_id)
    if main_id == game_id:
        main_id = await choose_main(db, user_id, group - {game_id})
    await _rebuild_star(db, user_id, graph, group, main_id, origin=actor.origin, stamp=stamp)
    await _mark_primary(db, user_id, group, main_id)
    return await unmerge_identity(
        db, user_id, game_id, actor, keep_separate=keep_separate, now=stamp
    )


async def choose_main(db: AsyncSession, user_id: UUID, candidates: set[UUID]) -> UUID:
    """The copy that best represents a group: furthest progressed, then most played.

    Used when the user did not pick one, for example when a suggestion is
    accepted. Ties fall back to the oldest record so the choice is stable."""
    rows = (
        (
            await db.execute(
                select(Game).where(
                    Game.user_id == user_id, Game.id.in_(candidates), Game.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise LookupError("Game not found")
    metrics = await counters.reconcile(db, user_id, rows[0].id)
    playtime = {row.id: row.playtime_seconds for row in rows}
    for view in metrics["metrics"]["playtime"]["counters"]:
        if view["effective"] is not None and view["game_id"] in playtime:
            playtime[view["game_id"]] = max(playtime[view["game_id"]], int(view["effective"]))
    progress = {
        "MASTERED": 6,
        "BEATEN": 5,
        "PLAYED": 4,
        "PLAYING": 3,
        "ON_HOLD": 2,
        "DROPPED": 1,
        "BACKLOG": 0,
        "WISHLIST": -1,
    }

    def rank(row: Any) -> tuple[int, int, int, str]:
        status = getattr(row.status, "value", row.status)
        return (
            -playtime.get(row.id, 0),
            -progress.get(str(status), 0),
            row.created_at or 0,
            str(row.id),
        )

    return min(rows, key=rank).id
