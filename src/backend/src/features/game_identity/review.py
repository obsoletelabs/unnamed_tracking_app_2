"""Hybrid identity matching and the durable duplicate review queue (#99).

`run_matching` evaluates owned records pair by pair (see `matching.py`) and
persists the outcome as identity decisions: a conservative automatic
association, or an unresolved suggestion that is the queue entry. Because the
queue is the decision table, entries are stable across refresh, retry and
restart, and a user's merge or keep-separate (or an undo) supersedes them.
"""

# pylint: disable=too-many-locals,duplicate-code

import logging
import time
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
from src.features.game_identity.graph import (
    IdentityConflict,
    IdentityError,
    add_relationship,
    lock_user_identities,
    merge_graph,
    now_seconds,
)
from src.features.game_identity.matching import (
    COPY_RULE,
    COPY_RULE_VERSION,
    RULE,
    RULE_VERSION,
    Candidate,
    assess,
    assess_copy,
    base_title_keys,
    title_key,
)
from src.features.game_identity.same_copy import group_as_same_copy

logger = logging.getLogger(__name__)

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
    db: AsyncSession, user_id: UUID, only: set[UUID] | None = None
) -> tuple[dict[UUID, Candidate], dict[UUID, dict[str, Any]]]:
    """Summaries and match candidates of the live games (or just those in `only`).

    Reading the queue or the banner needs the few games named by open decisions, not the whole
    library, so those callers pass `only` and pay for what they show."""
    if only is not None and not only:
        return {}, {}
    games = select(*(getattr(Game, name) for name in _SUMMARY)).where(
        Game.user_id == user_id, Game.deleted_at.is_(None)
    )
    if only is not None:
        games = games.where(Game.id.in_(only))
    rows = (await db.execute(games.order_by(Game.id))).mappings()
    summaries = {row["id"]: dict(row) for row in rows}
    # Trusted = an open link to an available observation of a connected source.
    observed = (
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
    if only is not None:
        observed = observed.where(GameSourceObservationLink.game_id.in_(summaries))
    evidence = (await db.execute(observed)).all()
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
        for base in base_title_keys(item.title):
            groups.setdefault(("base", base), []).append(game_id)
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
    # One lock for the whole run, taken before anything is read, so every decision below is made
    # on state nobody else can change until this transaction ends.
    started = time.perf_counter()
    await lock_user_identities(db, user_id)
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
        rule, rule_version = RULE, RULE_VERSION
        if verdict.level not in ("auto", "review"):
            copy = assess_copy(found[first], found[second])
            if copy is None:
                result.skipped += 1
                continue
            verdict, rule, rule_version = copy, COPY_RULE, COPY_RULE_VERSION
        merge = verdict.level == "auto" and allow_automatic and inventory_complete
        if existing is not None and not merge and existing.rule_version == rule_version:
            result.skipped += 1  # already suggested by this rule; nothing to write
            continue
        outcome = await decisions.associate_automatically(
            db,
            user_id,
            first,
            second,
            rule=rule,
            rule_version=rule_version,
            confidence=verdict.confidence,
            reason=_reason(verdict.level, merge),
            evidence=verdict.evidence,
            merge=merge,
            graph=graph,
        )
        if outcome.outcome == "associated":
            result.associated += 1
            result.pairs.append(pair)
            edge = outcome.relationship
            if edge is not None and edge.from_game_id and edge.to_game_id:
                graph.parent[edge.from_game_id] = edge.to_game_id  # keep the graph current
                graph.edge[edge.from_game_id] = edge
            try:
                async with db.begin_nested():
                    await counters.group_same_copy(db, user_id, first, second, graph=graph)
            except IdentityError:
                pass  # the merge stands; the user can still group the counters by hand
        elif outcome.outcome == "suggested":
            result.suggested += 1
        else:
            result.skipped += 1
    logger.info(
        "identity matching user=%s games=%d associated=%d suggested=%d skipped=%d "
        "automatic_allowed=%s inventory_complete=%s seconds=%.2f",
        user_id,
        len(found),
        result.associated,
        result.suggested,
        result.skipped,
        allow_automatic,
        inventory_complete,
        time.perf_counter() - started,
    )
    return result


def _reason(level: str, merged: bool) -> str:
    if merged:
        return (
            "Same title, release date, edition and platform with identical playtime and last played"
        )
    if level == "auto":
        return "Strong match held for review because the inventory was not confirmed complete"
    if level == "copy":
        return "Looks like another copy or edition of the same game"
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


def _summary(
    row: dict[str, Any], reported_by: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {**{name: row[name] for name in _SUMMARY}, "reported_by": reported_by or []}


async def _reported_by(
    db: AsyncSession, user_id: UUID, games: set[UUID]
) -> dict[UUID, list[dict[str, Any]]]:
    """Which connections currently report each game: Playnite, the Epic plugin, a Steam sync...

    `Game.source` is the store a record says it belongs to, which Playnite fills in with the
    store's name, so it cannot tell a Steam game synced from Steam from one Playnite found."""
    if not games:
        return {}
    rows = await db.execute(
        select(
            GameSourceObservationLink.game_id,
            GameSourceConnection.source_kind,
            GameSourceConnection.source,
            GameSourceConnection.label,
            GameSourceConnection.disconnected_at,
        )
        .join(
            GameSourceObservation,
            GameSourceObservation.id == GameSourceObservationLink.observation_id,
        )
        .join(GameSourceConnection, GameSourceConnection.id == GameSourceObservation.connection_id)
        .where(
            GameSourceObservationLink.user_id == user_id,
            GameSourceObservationLink.unlinked_at.is_(None),
            GameSourceObservationLink.game_id.in_(games),
        )
        .order_by(GameSourceConnection.source, GameSourceConnection.label)
    )
    found: dict[UUID, list[dict[str, Any]]] = {}
    for game_id, kind, source, label, disconnected_at in rows.all():
        entry = {
            "kind": kind,
            "source": source,
            "label": label or source,
            "disconnected": disconnected_at is not None,
        }
        if entry not in found.setdefault(game_id, []):
            found[game_id].append(entry)
    return found


async def queue(db: AsyncSession, user_id: UUID, limit: int = 50) -> dict[str, Any]:
    """Open review entries: persisted automatic suggestions both of whose records still exist."""
    graph = await merge_graph(db, user_id, readonly=True)
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
    rows = list(open_rows)
    found, summaries = await _candidates(
        db, user_id, {game for row in rows for game in _members(row)}
    )
    dismissed = await _dismissed(db, user_id)
    reported = await _reported_by(db, user_id, set(summaries))
    entries: list[dict[str, Any]] = []
    # Once copies are grouped, a third record matches each of them; ask once per
    # pair of groups (the strongest suggestion first, as the rows are ordered).
    asked: set[tuple[UUID, UUID]] = set()
    for row in rows:
        pair = _members(row)
        if (
            pair[0] not in found
            or pair[1] not in found
            or pair in dismissed
            or graph.root(pair[0]) == graph.root(pair[1])
        ):
            continue
        groups = _ordered(graph.root(pair[0]), graph.root(pair[1]))
        if groups in asked:
            continue
        asked.add(groups)
        entries.append(
            {
                "id": row.id,
                # "copy": another platform, store or edition; "duplicate": the same copy twice
                "kind": "copy" if (row.evidence or {}).get("level") == "copy" else "duplicate",
                "confidence": float(row.confidence or 0),
                "reason": row.reason,
                "evidence": row.evidence,
                "created_at": row.created_at,
                "first": _summary(summaries[pair[0]], reported.get(pair[0])),
                "second": _summary(summaries[pair[1]], reported.get(pair[1])),
                "differences": _differences(summaries[pair[0]], summaries[pair[1]]),
            }
        )
        if len(entries) > limit:  # one more than the page tells the caller there is more
            break
    return {"entries": entries[:limit], "has_more": len(entries) > limit}


async def automatic_merges(
    db: AsyncSession, user_id: UUID, limit: int = 20
) -> list[dict[str, Any]]:
    """Active automatic associations the user can still undo (the banner source)."""
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
    pairs = rows.all()
    _, summaries = await _candidates(
        db,
        user_id,
        {g for _, edge in pairs for g in (edge.from_game_id, edge.to_game_id) if g is not None},
    )
    reported = await _reported_by(db, user_id, set(summaries))
    merged = []
    for decision, edge in pairs:
        if edge.from_game_id not in summaries or edge.to_game_id not in summaries:
            continue
        merged.append(
            {
                "id": decision.id,
                "confidence": float(decision.confidence or 0),
                "reason": decision.reason,
                "evidence": decision.evidence,
                "created_at": decision.created_at,
                "absorbed": _summary(summaries[edge.from_game_id], reported.get(edge.from_game_id)),
                "into": _summary(summaries[edge.to_game_id], reported.get(edge.to_game_id)),
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
    db: AsyncSession,
    user_id: UUID,
    decision_id: UUID,
    target_id: UUID | None = None,
    display: str | None = None,
    same_copy: bool = False,
) -> None:
    """Queue action: the person says these are the same game.

    `same_copy` says the two records are one copy reported by two sources (Playnite and a
    store's own sync), so their playtime is counted once. Different stores or platforms are
    different copies and are refused.

    `display` is how the absorbed copy shows in the library: folded under the main entry
    (default), also listed on its own ("link"), or hidden."""
    decision = await _open_decision(db, user_id, decision_id, _UNRESOLVED)
    first, second = _members(decision)
    if target_id not in (None, first, second):
        raise decisions.IdentityInvalid("The target must be one of the two games")
    target = target_id or first
    source = second if target == first else first
    actor = decisions.Actor(reason="Confirmed from the review queue")
    await decisions.merge_identities(db, user_id, source, target, actor)
    if same_copy:
        await group_as_same_copy(db, user_id, source, target)
    if target_id is None:
        # Nobody picked the main copy: use the furthest progressed, most played one
        # rather than whichever record happened to sort first (#274).
        group = (await merge_graph(db, user_id)).group(target)
        await decisions.make_main(
            db, user_id, await decisions.choose_main(db, user_id, group), actor
        )
    if display is not None and display != "default":
        # After the main copy is settled: choosing it can re-point the merge edge.
        merged = await merge_graph(db, user_id)
        folded = source if merged.root(source) != source else target
        edge = merged.edge.get(folded)
        if edge is not None:
            edge.display = display
            edge.updated_at = now_seconds()
            await db.flush()


async def resolve_related(
    db: AsyncSession,
    user_id: UUID,
    decision_id: UUID,
    *,
    kind: str,
    from_id: UUID,
    label: str = "",
    display: str = "default",
) -> None:
    """Queue action: different games, but one is a remake, port, DLC... of the other.

    Both stay separate library entries (so this is remembered like "keep separate") and the
    typed relationship is recorded from `from_id` to the other game of the pair."""
    decision = await _open_decision(db, user_id, decision_id, _UNRESOLVED)
    first, second = _members(decision)
    if from_id not in (first, second):
        raise decisions.IdentityInvalid("The relationship must start at one of the two games")
    await add_relationship(
        db,
        user_id,
        kind,
        from_id,
        second if from_id == first else first,
        label=label,
        display=display,
    )
    await game_duplicates.keep_both(db, user_id, first, second)


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
    "resolve_related",
    "resolve_separate",
    "run_matching",
    "title_key",
    "undo_automatic",
]
