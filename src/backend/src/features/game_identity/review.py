"""Hybrid identity matching and the durable duplicate review queue (#99).

`run_matching` evaluates owned records pair by pair (see `matching.py`) and
persists the outcome as identity decisions: a conservative automatic
association, or an unresolved suggestion that is the queue entry. Because the
queue is the decision table, entries are stable across refresh, retry and
restart, and a user's merge or keep-separate (or an undo) supersedes them.
"""

# pylint: disable=too-many-locals,duplicate-code

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_duplicate_dismissal import GameDuplicateDismissal
from src.database.models.game_identity import (
    DecisionActor,
    DecisionOutcome,
    DecisionSubject,
    GameIdentityDecision,
    GameRelationship,
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
    ObservationAvailability,
)
from src.features import game_duplicates
from src.features.game_identity import counters, decisions
from src.features.game_identity.graph import IdentityConflict, IdentityError, merge_graph
from src.features.game_identity.matching import (
    RULE,
    RULE_VERSION,
    Candidate,
    assess,
    title_key,
)

_PAIR = DecisionSubject.IDENTITY_PAIR.value
_AUTOMATIC = DecisionActor.AUTOMATIC.value
_UNRESOLVED = DecisionOutcome.UNRESOLVED.value
_AUTO_SAME = DecisionOutcome.AUTOMATIC_SAME.value
_SUMMARY = (
    "id",
    "title",
    "source",
    "platform",
    "release_date",
    "external_id",
    "provider_ids",
    "status",
    "playtime_seconds",
    "last_played_at",
    "parent_game_id",
    "relationship_type",
    "locked_fields",
)
_COMPARED = (
    "title",
    "source",
    "platform",
    "release_date",
    "playtime_seconds",
    "last_played_at",
    "external_id",
    "provider_ids",
    "status",
)


@dataclass
class RunResult:
    associated: int = 0
    suggested: int = 0
    skipped: int = 0
    pairs: list[tuple[UUID, UUID]] = field(default_factory=list)


def _members(decision: GameIdentityDecision) -> tuple[UUID, UUID]:
    if decision.first_game_id is None or decision.second_game_id is None:
        raise LookupError("This entry is not a pair of games.")
    return decision.first_game_id, decision.second_game_id


def _ordered(first: UUID, second: UUID) -> tuple[UUID, UUID]:
    return (first, second) if first < second else (second, first)


async def _candidates(
    db: AsyncSession, user_id: UUID
) -> tuple[dict[UUID, Candidate], dict[UUID, dict[str, Any]]]:
    rows = (
        await db.execute(
            select(*(getattr(Game, name) for name in _SUMMARY))
            .where(Game.user_id == user_id, Game.deleted_at.is_(None))
            .order_by(Game.id)
        )
    ).mappings()
    summaries = {row["id"]: dict(row) for row in rows}
    # Trusted = an open link to an available observation of a connected source.
    evidence = (
        await db.execute(
            select(
                GameSourceObservationLink.game_id,
                GameSourceObservation.observed_edition,
                GameSourceObservation.availability,
                GameSourceObservation.archived_at,
                GameSourceConnection.disconnected_at,
            )
            .join(
                GameSourceObservation,
                GameSourceObservation.id == GameSourceObservationLink.observation_id,
            )
            .join(
                GameSourceConnection,
                GameSourceConnection.id == GameSourceObservation.connection_id,
            )
            .where(
                GameSourceObservationLink.user_id == user_id,
                GameSourceObservationLink.unlinked_at.is_(None),
            )
        )
    ).all()
    trusted: set[UUID] = set()
    edition: dict[UUID, str] = {}
    for game_id, observed, availability, archived, disconnected in evidence:
        if (
            availability == ObservationAvailability.AVAILABLE.value
            and archived is None
            and disconnected is None
        ):
            trusted.add(game_id)
        if observed:
            edition.setdefault(game_id, observed)
    found = {
        game_id: Candidate(
            id=game_id,
            title=row["title"],
            source=row["source"],
            platform=row["platform"],
            release_date=row["release_date"],
            external_id=row["external_id"],
            provider_ids=row["provider_ids"] or {},
            playtime_seconds=row["playtime_seconds"] or 0,
            last_played_at=row["last_played_at"],
            parent_game_id=row["parent_game_id"],
            relationship_type=row["relationship_type"],
            observed_edition=edition.get(game_id),
            trusted=game_id in trusted,
        )
        for game_id, row in summaries.items()
    }
    return found, summaries


def _pairs(found: dict[UUID, Candidate]) -> set[tuple[UUID, UUID]]:
    groups: dict[tuple[str, ...], list[UUID]] = {}
    for game_id, item in found.items():
        if item.key:
            groups.setdefault(("title", item.key), []).append(game_id)
        for namespace, identity in item.provider_ids.items():
            if identity:
                groups.setdefault(("id", namespace, identity), []).append(game_id)
    return {_ordered(a, b) for members in groups.values() for a, b in combinations(members, 2)}


async def _current_pairs(
    db: AsyncSession, user_id: UUID
) -> dict[tuple[UUID, UUID], GameIdentityDecision]:
    rows = await db.scalars(
        select(GameIdentityDecision).where(
            GameIdentityDecision.user_id == user_id,
            GameIdentityDecision.subject_kind == _PAIR,
            GameIdentityDecision.superseded_at.is_(None),
        )
    )
    return {_members(row): row for row in rows}


async def _dismissed(db: AsyncSession, user_id: UUID) -> set[tuple[UUID, UUID]]:
    rows = await db.execute(
        select(GameDuplicateDismissal.first_game_id, GameDuplicateDismissal.second_game_id).where(
            GameDuplicateDismissal.user_id == user_id
        )
    )
    return {_ordered(a, b) for a, b in rows.all()}


async def run_matching(
    db: AsyncSession,
    user_id: UUID,
    *,
    allow_automatic: bool = True,
    inventory_complete: bool = True,
    game_ids: set[UUID] | None = None,
) -> RunResult:
    """Evaluate pairs and persist decisions. Idempotent; never overrides a person.

    Nothing merges automatically unless `inventory_complete` and the strict rule
    holds with both records trusted; otherwise an equally strong match is only
    queued. Pairs a person touched, or rejected, are skipped."""
    found, _ = await _candidates(db, user_id)
    current = await _current_pairs(db, user_id)
    dismissed = await _dismissed(db, user_id)
    graph = await merge_graph(db, user_id)
    result = RunResult()
    for pair in sorted(_pairs(found)):
        first, second = pair
        if game_ids is not None and not {first, second} & game_ids:
            continue
        existing = current.get(pair)
        touched = existing is not None and (
            existing.actor_kind != _AUTOMATIC or existing.outcome != _UNRESOLVED
        )
        if touched or pair in dismissed or graph.root(first) == graph.root(second):
            result.skipped += 1
            continue
        verdict = assess(found[first], found[second])
        if verdict.level not in ("auto", "review"):
            result.skipped += 1
            continue
        merge = verdict.level == "auto" and allow_automatic and inventory_complete
        outcome = await decisions.associate_automatically(
            db,
            user_id,
            first,
            second,
            rule=RULE,
            rule_version=RULE_VERSION,
            confidence=verdict.confidence,
            reason=_reason(verdict.level, merge),
            evidence=verdict.evidence,
            merge=merge,
        )
        if outcome.outcome == "associated":
            result.associated += 1
            result.pairs.append(pair)
            try:
                async with db.begin_nested():
                    await counters.group_same_copy(db, user_id, first, second)
            except IdentityError:
                pass  # the merge stands; the user can still group the counters by hand
            graph = await merge_graph(db, user_id)
        elif outcome.outcome == "suggested":
            result.suggested += 1
        else:
            result.skipped += 1
    return result


def _reason(level: str, merged: bool) -> str:
    if merged:
        return (
            "Same title, release date, edition and platform with identical playtime and last played"
        )
    if level == "auto":
        return "Strong match held for review because the inventory was not confirmed complete"
    return "Evidence suggests these may be the same game"


def _differences(first: dict[str, Any], second: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "field": name,
            "first": first[name],
            "second": second[name],
            "same": first[name] == second[name],
        }
        for name in _COMPARED
    ]


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {name: row[name] for name in _SUMMARY}


async def queue(db: AsyncSession, user_id: UUID, limit: int = 50) -> dict[str, Any]:
    """Open review entries: persisted automatic suggestions both of whose records still exist."""
    found, summaries = await _candidates(db, user_id)
    graph = await merge_graph(db, user_id)
    open_rows = await db.scalars(
        select(GameIdentityDecision)
        .where(
            GameIdentityDecision.user_id == user_id,
            GameIdentityDecision.subject_kind == _PAIR,
            GameIdentityDecision.outcome == _UNRESOLVED,
            GameIdentityDecision.actor_kind == _AUTOMATIC,
            GameIdentityDecision.superseded_at.is_(None),
        )
        .order_by(
            GameIdentityDecision.confidence.desc(),
            GameIdentityDecision.created_at,
            GameIdentityDecision.id,
        )
    )
    dismissed = await _dismissed(db, user_id)
    entries: list[dict[str, Any]] = []
    for row in open_rows:
        pair = _members(row)
        if (
            pair[0] not in found
            or pair[1] not in found
            or pair in dismissed
            or graph.root(pair[0]) == graph.root(pair[1])
        ):
            continue
        entries.append(
            {
                "id": row.id,
                "confidence": float(row.confidence or 0),
                "reason": row.reason,
                "evidence": row.evidence,
                "created_at": row.created_at,
                "first": _summary(summaries[pair[0]]),
                "second": _summary(summaries[pair[1]]),
                "differences": _differences(summaries[pair[0]], summaries[pair[1]]),
            }
        )
    return {"entries": entries[:limit], "has_more": len(entries) > limit}


async def automatic_merges(
    db: AsyncSession, user_id: UUID, limit: int = 20
) -> list[dict[str, Any]]:
    """Active automatic associations the user can still undo (the banner source)."""
    _, summaries = await _candidates(db, user_id)
    rows = await db.execute(
        select(GameIdentityDecision, GameRelationship)
        .join(GameRelationship, GameRelationship.decision_id == GameIdentityDecision.id)
        .where(
            GameIdentityDecision.user_id == user_id,
            GameIdentityDecision.outcome == _AUTO_SAME,
            GameIdentityDecision.superseded_at.is_(None),
            GameRelationship.ended_at.is_(None),
        )
        .order_by(GameIdentityDecision.created_at.desc(), GameIdentityDecision.id)
        .limit(limit)
    )
    merged = []
    for decision, edge in rows.all():
        if edge.from_game_id not in summaries or edge.to_game_id not in summaries:
            continue
        merged.append(
            {
                "id": decision.id,
                "confidence": float(decision.confidence or 0),
                "reason": decision.reason,
                "evidence": decision.evidence,
                "created_at": decision.created_at,
                "absorbed": _summary(summaries[edge.from_game_id]),
                "into": _summary(summaries[edge.to_game_id]),
            }
        )
    return merged


async def _open_decision(
    db: AsyncSession, user_id: UUID, decision_id: UUID, outcome: str
) -> GameIdentityDecision:
    decision = await db.scalar(
        select(GameIdentityDecision)
        .where(
            GameIdentityDecision.id == decision_id,
            GameIdentityDecision.user_id == user_id,
            GameIdentityDecision.superseded_at.is_(None),
        )
        .with_for_update()
    )
    if decision is None:
        raise LookupError("This suggestion was already resolved or no longer exists.")
    if decision.outcome != outcome or decision.actor_kind != _AUTOMATIC:
        raise IdentityConflict("This suggestion is not open")
    return decision


async def resolve_merge(
    db: AsyncSession, user_id: UUID, decision_id: UUID, target_id: UUID | None = None
) -> None:
    """Queue action: the person says these are the same game."""
    decision = await _open_decision(db, user_id, decision_id, _UNRESOLVED)
    first, second = _members(decision)
    if target_id not in (None, first, second):
        raise decisions.IdentityInvalid("The target must be one of the two games")
    target = target_id or first
    source = second if target == first else first
    await decisions.merge_identities(
        db, user_id, source, target, decisions.Actor(reason="Confirmed from the review queue")
    )


async def resolve_separate(db: AsyncSession, user_id: UUID, decision_id: UUID) -> None:
    """Queue action: keep both. Also records the legacy dismissal so the old list agrees."""
    decision = await _open_decision(db, user_id, decision_id, _UNRESOLVED)
    await game_duplicates.keep_both(db, user_id, *_members(decision))


async def undo_automatic(db: AsyncSession, user_id: UUID, decision_id: UUID) -> None:
    """Banner action: split the automatic association and remember they are separate."""
    decision = await _open_decision(db, user_id, decision_id, _AUTO_SAME)
    absorbed = UUID((decision.resulting_state or {}).get("absorbed", ""))
    await decisions.unmerge_identity(
        db,
        user_id,
        absorbed,
        decisions.Actor(reason="Undid an automatic association"),
        keep_separate=True,
    )
    await counters.ungroup_games(db, user_id, *_members(decision))
    await game_duplicates.keep_both(db, user_id, *_members(decision))


async def keep_automatic(db: AsyncSession, user_id: UUID, decision_id: UUID) -> None:
    """Banner action: the person confirms the association, which also clears the banner."""
    decision = await _open_decision(db, user_id, decision_id, _AUTO_SAME)
    first, second = _members(decision)
    await decisions.merge_identities(
        db,
        user_id,
        second,
        first,
        decisions.Actor(reason="Confirmed an automatic association"),
    )


__all__ = [
    "RunResult",
    "automatic_merges",
    "keep_automatic",
    "queue",
    "resolve_merge",
    "resolve_separate",
    "run_matching",
    "title_key",
    "undo_automatic",
]
