"""Counter groups, persistent adjustments and per-metric reconciliation (#96).

Identity and counter are separate questions. Identity (who is the same game) is
decided by #95; this module decides which observations report the *same counter*
and how the effective value of each metric is derived. Counters live on the
tracked identity (`games` row) that holds the observations; a merge only adds a
`same_game` edge, so every counter, member and adjustment survives merges, splits
and restarts, and a view over a merged group simply combines the counters of its
games. Observations of one counter are never summed; independent counters are
combined by the metric's own rule (see `policy`).

Every write takes the per-user identity lock and is idempotent; callers commit.
Reading through `reconcile` lazily creates one counter per observation that
reports a metric (the safe default: nothing is grouped until a user or a reviewed
rule says two observations count the same thing).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import null, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game_identity import (
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
)
from src.database.models.game_identity_tracking import (
    AdjustmentKind,
    FieldValueState,
    GameFieldValue,
    GameMetric,
    GameMetricAdjustment,
    GameMetricCounter,
    GameMetricCounterMember,
)
from src.features.game_identity import manual, playtime, policy
from src.features.game_identity.graph import (
    IdentityConflict,
    IdentityInvalid,
    MergeGraph,
    lock_user_identities,
    merge_graph,
    now_seconds,
    require_games,
)
from src.features.game_identity.policy import Reading

# metric -> observed fact names; the first one carries the freshness timestamp
METRIC_FACTS: dict[str, tuple[str, ...]] = {
    GameMetric.PLAYTIME.value: ("playtime_seconds",),
    GameMetric.LAST_PLAYED.value: ("last_played_at",),
    GameMetric.ACHIEVEMENTS.value: ("achievements_unlocked", "achievements_total"),
    GameMetric.COMPLETION.value: ("status",),
    GameMetric.OWNERSHIP.value: ("owned",),
    GameMetric.INSTALLED.value: ("installed",),
}
_PLAYTIME = GameMetric.PLAYTIME.value
_CARRIED = "carried:"
_REBASED = "rebased"


def _value_of(metric: str, observation: GameSourceObservation) -> tuple[Any, Any]:
    facts = observation.observed_facts or {}
    if metric == GameMetric.ACHIEVEMENTS.value:
        unlocked, total = facts.get("achievements_unlocked"), facts.get("achievements_total")
        value = unlocked if unlocked is not None else total
        return value, {"unlocked": unlocked, "total": total}
    return facts.get(METRIC_FACTS[metric][0]), None


def _reading(
    metric: str,
    member: GameSourceObservation,
    connection: GameSourceConnection,
    priority: int,
) -> Reading:
    value, detail = _value_of(metric, member)
    fact = METRIC_FACTS[metric]
    stamps = member.field_observed_at or {}
    seen = [stamps[name] for name in fact if name in stamps]
    return Reading(
        observation_id=member.id,
        source=connection.source,
        value=value,
        observed_at=max(seen) if seen else None,
        priority=priority,
        detail=detail,
        available=member.availability == "available",
    )


def _select(metric: str, readings: list[Reading]) -> Reading | None:
    if metric == GameMetric.LAST_PLAYED.value:
        return policy.pick_latest(readings)
    return policy.pick_preferred(readings)


_SYNC_SET = 200  # games reconciled per set when many groups are synced at once


@dataclass
class _CounterData:
    counter: GameMetricCounter
    members: list[GameMetricCounterMember]
    readings: list[Reading]
    adjustment: GameMetricAdjustment | None

    @property
    def winner(self) -> Reading | None:
        return _select(self.counter.metric, self.readings)


def _stamp(now: int | None) -> int:
    return now_seconds() if now is None else now


def _retire(row: GameMetricAdjustment | GameMetricCounterMember, stamp: int) -> None:
    if isinstance(row, GameMetricAdjustment):
        row.superseded_at = max(stamp, row.created_at)
    else:
        row.removed_at = max(stamp, row.added_at)


async def _active_adjustments(
    db: AsyncSession, counter_ids: list[UUID]
) -> dict[UUID, GameMetricAdjustment]:
    return {
        a.counter_id: a
        for a in await db.scalars(
            select(GameMetricAdjustment).where(
                GameMetricAdjustment.counter_id.in_(counter_ids),
                GameMetricAdjustment.superseded_at.is_(None),
            )
        )
    }


async def _load(
    db: AsyncSession,
    user_id: UUID,
    games: set[UUID],
    *,
    counter_ids: set[UUID] | None = None,
    metric: str | None = None,
) -> list[_CounterData]:
    statement = select(GameMetricCounter).where(
        GameMetricCounter.user_id == user_id, GameMetricCounter.archived_at.is_(None)
    )
    if metric is not None:
        statement = statement.where(GameMetricCounter.metric == metric)
    if counter_ids is not None:
        statement = statement.where(GameMetricCounter.id.in_(counter_ids))
    else:
        statement = statement.where(GameMetricCounter.game_id.in_(games))
    counters = list(
        await db.scalars(statement.order_by(GameMetricCounter.created_at, GameMetricCounter.id))
    )
    if not counters:
        return []
    ids = [c.id for c in counters]
    members = list(
        await db.scalars(
            select(GameMetricCounterMember)
            .where(
                GameMetricCounterMember.counter_id.in_(ids),
                GameMetricCounterMember.removed_at.is_(None),
            )
            .order_by(GameMetricCounterMember.added_at, GameMetricCounterMember.id)
        )
    )
    observed = {
        o.id: o
        for o in await db.scalars(
            select(GameSourceObservation).where(
                GameSourceObservation.id.in_({m.observation_id for m in members})
            )
        )
    }
    connections = {
        c.id: c
        for c in await db.scalars(
            select(GameSourceConnection).where(
                GameSourceConnection.id.in_({o.connection_id for o in observed.values()})
            )
        )
    }
    adjustments = await _active_adjustments(db, ids)
    result = []
    for counter in counters:
        mine = [m for m in members if m.counter_id == counter.id]
        readings = [
            _reading(
                counter.metric,
                observed[m.observation_id],
                connections[observed[m.observation_id].connection_id],
                m.priority,
            )
            for m in mine
        ]
        result.append(_CounterData(counter, mine, readings, adjustments.get(counter.id)))
    return result


async def _open_links(
    db: AsyncSession,
    user_id: UUID,
    *,
    games: set[UUID] | None = None,
    observations: set[UUID] | None = None,
) -> dict[UUID, UUID]:
    statement = select(
        GameSourceObservationLink.observation_id, GameSourceObservationLink.game_id
    ).where(
        GameSourceObservationLink.user_id == user_id,
        GameSourceObservationLink.unlinked_at.is_(None),
    )
    if games is not None:
        statement = statement.where(GameSourceObservationLink.game_id.in_(games))
    if observations is not None:
        statement = statement.where(GameSourceObservationLink.observation_id.in_(observations))
    return dict((await db.execute(statement)).tuples().all())


def _new_adjustment(
    source: GameMetricAdjustment,
    counter_id: UUID,
    base: Reading | None,
    reason: str,
    stamp: int,
) -> GameMetricAdjustment:
    return GameMetricAdjustment(
        user_id=source.user_id,
        counter_id=counter_id,
        metric=source.metric,
        kind=source.kind,
        offset_value=source.offset_value,
        override_value=null() if source.override_value is None else source.override_value,
        base_observation_id=base.observation_id if base else source.base_observation_id,
        base_value=base.value if base else source.base_value,
        reason=reason,
        created_at=stamp,
    )


async def _ensure_members(db: AsyncSession, user_id: UUID, games: set[UUID], stamp: int) -> None:
    """One counter per observation and metric it reports, on the game it is linked to."""
    links = await _open_links(db, user_id, games=games)
    if not links:
        return
    observed = list(
        await db.scalars(
            select(GameSourceObservation)
            .where(GameSourceObservation.id.in_(links))
            .order_by(GameSourceObservation.first_observed_at, GameSourceObservation.id)
        )
    )
    taken = {
        (m.observation_id, m.metric)
        for m in await db.scalars(
            select(GameMetricCounterMember).where(
                GameMetricCounterMember.observation_id.in_(links),
                GameMetricCounterMember.removed_at.is_(None),
            )
        )
    }
    for observation in observed:
        for metric in METRIC_FACTS:
            if (observation.id, metric) in taken or _value_of(metric, observation)[0] is None:
                continue
            # The ID is chosen here so the counter and its member go out in one flush.
            counter = GameMetricCounter(
                id=uuid4(),
                user_id=user_id,
                game_id=links[observation.id],
                metric=metric,
                created_at=stamp,
            )
            db.add(counter)
            db.add(
                GameMetricCounterMember(
                    user_id=user_id,
                    counter_id=counter.id,
                    metric=metric,
                    observation_id=observation.id,
                    priority=policy.DEFAULT_PRIORITY,
                    added_at=stamp,
                )
            )
    await db.flush()


async def _rebase(db: AsyncSession, data: list[_CounterData], stamp: int) -> None:
    """Keep the user's offset when the selected base source changed, and record it."""
    for item in data:
        adjustment, winner = item.adjustment, item.winner
        if (
            adjustment is None
            or winner is None
            or adjustment.kind != AdjustmentKind.OFFSET.value
            or adjustment.base_observation_id == winner.observation_id
        ):
            continue
        _retire(adjustment, stamp)
        await db.flush()
        replacement = _new_adjustment(
            adjustment,
            item.counter.id,
            winner,
            adjustment.reason
            if adjustment.reason and adjustment.reason.startswith(_CARRIED)
            else f"{_REBASED}:{adjustment.base_observation_id}",
            stamp,
        )
        db.add(replacement)
        await db.flush()
        item.adjustment = replacement


async def _carry_adjustments(
    db: AsyncSession,
    departed: list[tuple[_CounterData, GameMetricCounterMember]],
    stamp: int,
) -> None:
    """An adjustment follows the observation it was made against to its new counter."""
    for item, member in departed:
        adjustment = item.adjustment
        if adjustment is None or adjustment.base_observation_id != member.observation_id:
            continue
        successor = await db.scalar(
            select(GameMetricCounterMember.counter_id).where(
                GameMetricCounterMember.observation_id == member.observation_id,
                GameMetricCounterMember.metric == member.metric,
                GameMetricCounterMember.removed_at.is_(None),
            )
        )
        if successor is None:
            continue
        _retire(adjustment, stamp)
        await db.flush()
        db.add(_new_adjustment(adjustment, successor, None, f"{_CARRIED}{item.counter.id}", stamp))
        await db.flush()


async def _sync(
    db: AsyncSession, user_id: UUID, graph_: MergeGraph, game_id: UUID, stamp: int
) -> set[UUID]:
    """Make memberships match the current links; returns the games in the group."""
    return await _sync_games(db, user_id, graph_, graph_.group(game_id), stamp)


async def _sync_games(
    db: AsyncSession, user_id: UUID, graph_: MergeGraph, games: set[UUID], stamp: int
) -> set[UUID]:
    """`_sync` for every game in `games`, which may span many merge groups.

    Memberships, counters and adjustments are keyed by counter and observation, and each
    counter is checked against its own group, so reconciling several groups together does
    exactly what reconciling them one by one does, in a constant number of statements."""
    data = await _load(db, user_id, games)
    links = await _open_links(
        db, user_id, observations={m.observation_id for d in data for m in d.members}
    )
    departed: list[tuple[_CounterData, GameMetricCounterMember]] = []
    for item in data:
        group = graph_.group(item.counter.game_id)
        for member in item.members:
            if links.get(member.observation_id) not in group:
                _retire(member, stamp)
                departed.append((item, member))
    await db.flush()
    for item in data:
        item.members = [m for m in item.members if m.removed_at is None]
        if not item.members:
            item.counter.archived_at = max(stamp, item.counter.created_at)
    reach = set(games) | set(
        (
            await _open_links(db, user_id, observations={m.observation_id for _, m in departed})
        ).values()
    )
    await _ensure_members(db, user_id, reach, stamp)
    await _carry_adjustments(db, departed, stamp)
    fresh = await _load(db, user_id, games)
    await _rebase(db, [d for d in fresh if d.counter.metric == _PLAYTIME], stamp)
    return games


async def sync_counters(
    db: AsyncSession, user_id: UUID, game_id: UUID, *, now: int | None = None
) -> None:
    """Materialise counters and reconcile memberships for an identity's group."""
    await lock_user_identities(db, user_id)
    await require_games(db, user_id, game_id)
    await _sync(db, user_id, await merge_graph(db, user_id), game_id, _stamp(now))


async def sync_many(
    db: AsyncSession, user_id: UUID, game_ids: Collection[UUID], *, now: int | None = None
) -> int:
    """`sync_counters` for many games with one merge graph (the library backfill, #102).

    Groups are reconciled together in bounded sets rather than one statement sequence per
    game, so a 20,000-game library costs hundreds of statements instead of hundreds of
    thousands. Returns how many merge groups were reconciled."""
    await lock_user_identities(db, user_id)
    graph_ = await merge_graph(db, user_id, readonly=True)
    stamp = _stamp(now)
    done: set[UUID] = set()
    pending: set[UUID] = set()
    groups = 0
    for game_id in game_ids:
        if game_id in done:
            continue
        group = graph_.group(game_id)
        done |= group
        pending |= group
        groups += 1
        if len(pending) >= _SYNC_SET:
            await _sync_games(db, user_id, graph_, pending, stamp)
            pending = set()
    if pending:
        await _sync_games(db, user_id, graph_, pending, stamp)
    return groups


async def group_same_copy(
    db: AsyncSession,
    user_id: UUID,
    first_id: UUID,
    second_id: UUID,
    *,
    now: int | None = None,
    graph: MergeGraph | None = None,
) -> int:
    """After an automatic association (identical playtime and last played): count the
    evidence metrics once instead of summing two reports of one copy. Returns groupings made.
    Pass the caller's `graph` to avoid reloading it for every pair of a large run."""
    stamp = _stamp(now)
    graph_ = graph or await merge_graph(db, user_id)
    await _sync(db, user_id, graph_, first_id, stamp)
    data = await _load(db, user_id, {first_id, second_id})
    made = 0
    for metric in (_PLAYTIME, GameMetric.LAST_PLAYED.value):
        mine = [d for d in data if d.counter.metric == metric and d.counter.game_id == first_id]
        theirs = [d for d in data if d.counter.metric == metric and d.counter.game_id == second_id]
        if len(mine) == 1 and len(theirs) == 1:
            await group_counters(db, user_id, mine[0].counter.id, theirs[0].counter.id, now=stamp)
            made += 1
    return made


async def ungroup_games(
    db: AsyncSession, user_id: UUID, first_id: UUID, second_id: UUID, *, now: int | None = None
) -> int:
    """After two games were split: give each game's observations their own counters again.

    Reverses `group_same_copy` (and any manual grouping across the pair), so each game
    reports its own playtime. Returns how many observations were separated."""
    stamp = _stamp(now)
    separated = 0
    for owner, other in ((first_id, second_id), (second_id, first_id)):
        data = await _load(db, user_id, {owner})
        links = await _open_links(
            db, user_id, observations={m.observation_id for d in data for m in d.members}
        )
        for item in data:
            if item.counter.game_id != owner:
                continue
            for member in item.members:
                if links.get(member.observation_id) == other:
                    await ungroup_observation(
                        db, user_id, item.counter.id, member.observation_id, now=stamp
                    )
                    separated += 1
    graph_ = await merge_graph(db, user_id)
    for game_id in (first_id, second_id):
        await _sync(db, user_id, graph_, game_id, stamp)
    return separated


async def follow_observation(
    db: AsyncSession, user_id: UUID, observation_id: UUID, *, now: int | None = None
) -> None:
    """After an observation moved: re-home its memberships and carry its adjustments."""
    stamp = _stamp(now)
    graph_ = await merge_graph(db, user_id)
    games = set(
        await db.scalars(
            select(GameMetricCounter.game_id)
            .join(
                GameMetricCounterMember,
                GameMetricCounterMember.counter_id == GameMetricCounter.id,
            )
            .where(
                GameMetricCounterMember.observation_id == observation_id,
                GameMetricCounterMember.removed_at.is_(None),
            )
        )
    )
    links = await _open_links(db, user_id, observations={observation_id})
    games |= set(links.values())
    done: set[UUID] = set()
    for game in games:
        if game not in done:
            done |= await _sync(db, user_id, graph_, game, stamp)


# --- user-visible changes -------------------------------------------------------------------


async def _counter(db: AsyncSession, user_id: UUID, counter_id: UUID) -> GameMetricCounter:
    counter = await db.scalar(
        select(GameMetricCounter)
        .where(GameMetricCounter.id == counter_id, GameMetricCounter.user_id == user_id)
        .with_for_update()
    )
    if counter is None:
        raise LookupError("Counter not found")
    return counter


async def _one(db: AsyncSession, user_id: UUID, counter: GameMetricCounter) -> _CounterData:
    loaded = await _load(db, user_id, set(), counter_ids={counter.id})
    if not loaded:
        raise IdentityConflict("This counter is archived")
    return loaded[0]


async def set_priority(
    db: AsyncSession,
    user_id: UUID,
    counter_id: UUID,
    observation_id: UUID,
    priority: int,
    *,
    now: int | None = None,
) -> None:
    """Choose which source wins inside a counter (lower number wins)."""
    stamp = _stamp(now)
    await lock_user_identities(db, user_id)
    counter = await _counter(db, user_id, counter_id)
    member = await db.scalar(
        select(GameMetricCounterMember).where(
            GameMetricCounterMember.counter_id == counter_id,
            GameMetricCounterMember.observation_id == observation_id,
            GameMetricCounterMember.removed_at.is_(None),
        )
    )
    if member is None:
        raise LookupError("Observation is not in this counter")
    member.priority = priority
    await db.flush()
    if counter.metric == _PLAYTIME:
        await _rebase(db, [await _one(db, user_id, counter)], stamp)


async def group_counters(
    db: AsyncSession,
    user_id: UUID,
    target_id: UUID,
    source_id: UUID,
    *,
    now: int | None = None,
) -> UUID:
    """Declare that two counters report the same thing, so they are reconciled, not summed.

    The source counter is archived, not deleted; its adjustment is carried to the
    target when the target has none, and stays recoverable otherwise."""
    stamp = _stamp(now)
    await lock_user_identities(db, user_id)
    if target_id == source_id:
        raise IdentityInvalid("A counter cannot be grouped with itself")
    target, source = await _counter(db, user_id, target_id), await _counter(db, user_id, source_id)
    if target.metric != source.metric:
        raise IdentityInvalid("Only counters of the same metric can be grouped")
    graph_ = await merge_graph(db, user_id)
    if graph_.root(target.game_id) != graph_.root(source.game_id):
        raise IdentityConflict("Merge the two games before grouping their counters")
    target_data, source_data = await _one(db, user_id, target), await _one(db, user_id, source)
    for member in source_data.members:
        _retire(member, stamp)
    await db.flush()
    for member in source_data.members:
        db.add(
            GameMetricCounterMember(
                user_id=user_id,
                counter_id=target.id,
                metric=target.metric,
                observation_id=member.observation_id,
                priority=member.priority,
                added_at=stamp,
            )
        )
    source.archived_at = max(stamp, source.created_at)
    await db.flush()
    if source_data.adjustment is not None and target_data.adjustment is None:
        db.add(
            _new_adjustment(
                source_data.adjustment, target.id, None, f"{_CARRIED}{source.id}", stamp
            )
        )
        await db.flush()
    refreshed = await _one(db, user_id, target)
    if target.metric == _PLAYTIME:
        await _rebase(db, [refreshed], stamp)
    return target.id


async def ungroup_observation(
    db: AsyncSession,
    user_id: UUID,
    counter_id: UUID,
    observation_id: UUID,
    *,
    now: int | None = None,
) -> UUID:
    """Give an observation its own counter again (undoes a grouping)."""
    stamp = _stamp(now)
    await lock_user_identities(db, user_id)
    counter = await _counter(db, user_id, counter_id)
    data = await _one(db, user_id, counter)
    member = next((m for m in data.members if m.observation_id == observation_id), None)
    if member is None:
        raise LookupError("Observation is not in this counter")
    if len(data.members) == 1:
        return counter.id
    _retire(member, stamp)
    previous = await db.scalar(
        select(GameMetricCounter)
        .join(GameMetricCounterMember, GameMetricCounterMember.counter_id == GameMetricCounter.id)
        .where(
            GameMetricCounterMember.observation_id == observation_id,
            GameMetricCounterMember.metric == counter.metric,
            GameMetricCounterMember.removed_at.is_not(None),
            GameMetricCounter.archived_at.is_not(None),
            GameMetricCounter.user_id == user_id,
        )
        .order_by(GameMetricCounterMember.removed_at.desc())
        .limit(1)
    )
    if previous is not None:
        previous.archived_at = None
        restored = previous
    else:
        links = await _open_links(db, user_id, observations={observation_id})
        restored = GameMetricCounter(
            user_id=user_id,
            game_id=links.get(observation_id, counter.game_id),
            metric=counter.metric,
            created_at=stamp,
        )
        db.add(restored)
    await db.flush()
    db.add(
        GameMetricCounterMember(
            user_id=user_id,
            counter_id=restored.id,
            metric=counter.metric,
            observation_id=observation_id,
            priority=member.priority,
            added_at=stamp,
        )
    )
    adopted = await db.scalar(
        select(GameMetricAdjustment).where(
            GameMetricAdjustment.counter_id == counter.id,
            GameMetricAdjustment.superseded_at.is_(None),
            GameMetricAdjustment.reason == f"{_CARRIED}{restored.id}",
        )
    )
    if adopted is not None:
        _retire(adopted, stamp)
    await db.flush()
    return restored.id


async def _store_adjustment(
    db: AsyncSession,
    data: _CounterData,
    *,
    kind: str | None,
    offset: int | None,
    override: Any,
    reason: str | None,
    stamp: int,
) -> bool:
    """Replace the counter's adjustment; returns False when nothing changed."""
    winner = data.winner
    current = data.adjustment
    base_id = winner.observation_id if winner else None
    base_value = winner.value if winner else None
    if (
        current is not None
        and (current.kind, current.offset_value, current.override_value)
        == (
            kind,
            offset,
            override,
        )
        and current.base_observation_id == base_id
    ):
        return False
    if current is None and kind is None:
        return False
    if current is not None:
        _retire(current, stamp)
        await db.flush()
    if kind is not None:
        db.add(
            GameMetricAdjustment(
                user_id=data.counter.user_id,
                counter_id=data.counter.id,
                metric=data.counter.metric,
                kind=kind,
                offset_value=offset,
                override_value=null() if override is None else override,
                base_observation_id=base_id,
                base_value=base_value,
                reason=reason,
                created_at=stamp,
            )
        )
    await db.flush()
    return True


async def adjust_counter(
    db: AsyncSession,
    user_id: UUID,
    counter_id: UUID,
    desired_seconds: int,
    *,
    reason: str | None = None,
    now: int | None = None,
) -> bool:
    """Make a playtime counter read `desired_seconds`, remembered as an offset.

    Editing 200h to 190h stores -10h against the selected base; a later source
    report of 210h reads as 200h. Repeating the call is a no-op. Returns whether
    anything changed."""
    stamp = _stamp(now)
    await lock_user_identities(db, user_id)
    counter = await _counter(db, user_id, counter_id)
    if counter.metric != _PLAYTIME:
        raise IdentityInvalid("Only playtime can be adjusted")
    if desired_seconds < 0 or desired_seconds > 2_000_000_000:
        raise IdentityInvalid("Playtime must be between 0 and 2,000,000,000 seconds")
    data = await _one(db, user_id, counter)
    winner = data.winner
    if winner is None:
        return await _store_adjustment(
            db,
            data,
            kind=AdjustmentKind.OVERRIDE.value,
            offset=None,
            override=desired_seconds,
            reason=reason,
            stamp=stamp,
        )
    base = int(winner.value)
    if desired_seconds == base:
        return await _store_adjustment(
            db, data, kind=None, offset=None, override=None, reason=reason, stamp=stamp
        )
    return await _store_adjustment(
        db,
        data,
        kind=AdjustmentKind.OFFSET.value,
        offset=policy.offset_for(desired_seconds, base),
        override=None,
        reason=reason,
        stamp=stamp,
    )


async def clear_adjustment(
    db: AsyncSession, user_id: UUID, counter_id: UUID, *, now: int | None = None
) -> bool:
    stamp = _stamp(now)
    await lock_user_identities(db, user_id)
    data = await _one(db, user_id, await _counter(db, user_id, counter_id))
    return await _store_adjustment(
        db, data, kind=None, offset=None, override=None, reason=None, stamp=stamp
    )


async def adjust_total(
    db: AsyncSession,
    user_id: UUID,
    game_id: UUID,
    desired_seconds: int,
    *,
    counter_id: UUID | None = None,
    reason: str | None = None,
    now: int | None = None,
) -> bool:
    """Edit the merged total: the difference lands on one counter (the largest by default)."""
    await sync_counters(db, user_id, game_id, now=now)
    view = await reconcile(db, user_id, game_id, now=now, sync=False)
    counters = view["metrics"][_PLAYTIME]["counters"]
    if not counters:
        raise IdentityInvalid("No playtime has been reported for this game")
    chosen = (
        next((c for c in counters if c["counter_id"] == counter_id), None) if counter_id else None
    )
    if counter_id and chosen is None:
        raise LookupError("Counter not found for this game")
    chosen = chosen or max(counters, key=lambda c: (c["effective"] or 0, str(c["counter_id"])))
    total = view["metrics"][_PLAYTIME]["effective"] or 0
    wanted = (chosen["effective"] or 0) + (desired_seconds - total)
    if wanted < 0:
        raise IdentityInvalid("Edit a specific counter: this total cannot be reached from one")
    return await adjust_counter(db, user_id, chosen["counter_id"], wanted, reason=reason, now=now)


# --- reading --------------------------------------------------------------------------------


def _adjustment_view(item: _CounterData) -> dict[str, Any] | None:
    adjustment = item.adjustment
    if adjustment is None:
        return None
    winner = item.winner
    return {
        "id": adjustment.id,
        "kind": adjustment.kind,
        "offset": adjustment.offset_value,
        "override": adjustment.override_value,
        "base_observation_id": adjustment.base_observation_id,
        "base_value_when_set": adjustment.base_value,
        "current_base_observation_id": winner.observation_id if winner else None,
        "reason": adjustment.reason,
        "created_at": adjustment.created_at,
        "rebased": bool(adjustment.reason and adjustment.reason.startswith(_REBASED)),
        "carried": bool(adjustment.reason and adjustment.reason.startswith(_CARRIED)),
    }


async def playtime_by_game(db: AsyncSession, user_id: UUID, games: set[UUID]) -> dict[UUID, int]:
    """Reconciled playtime of games that already have playtime counters; read only, never creates."""
    if not games:
        return {}
    return playtime.totals(await _load(db, user_id, games, metric=_PLAYTIME))


def _counter_view(item: _CounterData) -> dict[str, Any]:
    metric = item.counter.metric
    winner, raw, effective = playtime.effective(item)
    priorities = {m.observation_id: m.priority for m in item.members}
    return {
        "counter_id": item.counter.id,
        "game_id": item.counter.game_id,
        "metric": metric,
        "label": item.counter.label,
        "raw": raw,
        "effective": effective,
        "selected_observation_id": winner.observation_id if winner else None,
        "selected_source": winner.source if winner else None,
        "detail": winner.detail if winner else None,
        "members": [
            {
                "observation_id": r.observation_id,
                "source": r.source,
                "value": r.value,
                "observed_at": r.observed_at,
                "priority": priorities[r.observation_id],
                "selected": winner is not None and r.observation_id == winner.observation_id,
                "available": r.available,
            }
            for r in item.readings
        ],
        "conflicts": policy.losers(item.readings, winner),
        "adjustment": _adjustment_view(item),
    }


async def _field(
    db: AsyncSession, user_id: UUID, games: set[UUID], name: str
) -> dict[str, Any] | None:
    row = await db.scalar(
        select(GameFieldValue)
        .where(
            GameFieldValue.user_id == user_id,
            GameFieldValue.game_id.in_(games),
            GameFieldValue.field == name,
            GameFieldValue.state == FieldValueState.CURRENT.value,
            GameFieldValue.origin == "user",
        )
        .order_by(GameFieldValue.edited_at.desc().nulls_last(), GameFieldValue.id)
        .limit(1)
    )
    if row is None:
        return None
    return {
        "value": row.value,
        "origin": row.origin,
        "edited_at": row.edited_at,
        "game_id": row.game_id,
    }


def _total(metric: str, views: list[dict[str, Any]]) -> dict[str, Any]:
    values = [v["effective"] for v in views if v["effective"] is not None]
    if metric == _PLAYTIME:
        return {"effective": sum(values) if values else None}
    if metric == GameMetric.LAST_PLAYED.value:
        return {"effective": max(values) if values else None}
    if metric == GameMetric.OWNERSHIP.value:
        return {"effective": policy.combine_any_true(values)}
    if metric == GameMetric.COMPLETION.value:
        done = sum(1 for v in views if policy.is_completed(v["effective"]))
        return {
            "effective": None if not values else done > 0,
            "completed": done,
            "of": len(values),
            "summary": f"Completed on {done} of {len(values)}" if values else None,
        }
    if metric == GameMetric.INSTALLED.value:
        return {
            "effective": None,
            "per_source": [
                {
                    "source": v["selected_source"],
                    "installed": v["effective"],
                    "counter_id": v["counter_id"],
                }
                for v in views
                if v["effective"] is not None
            ],
        }
    details = [v["detail"] for v in views if v["detail"]]  # achievements: never summed
    unlocked = [d["unlocked"] for d in details if d["unlocked"] is not None]
    totals = [d["total"] for d in details if d["total"] is not None]
    return {
        "effective": max(unlocked) if unlocked else None,
        "unlocked": max(unlocked) if unlocked else None,
        "total": max(totals) if totals else None,
        "per_counter": [
            {
                "source": v["selected_source"],
                "unlocked": v["detail"]["unlocked"],
                "total": v["detail"]["total"],
            }
            for v in views
            if v["detail"]
        ],
        "exact": False,
    }


async def reconcile(
    db: AsyncSession,
    user_id: UUID,
    game_id: UUID,
    *,
    now: int | None = None,
    sync: bool = True,
) -> dict[str, Any]:
    """Effective metrics of an identity group, with raw values, adjustments and conflicts.

    Raw source values, the effective value, the stored adjustment and why each
    losing reading lost are all returned so the UI can explain every number."""
    if sync:
        await sync_counters(db, user_id, game_id, now=now)
    else:
        await require_games(db, user_id, game_id)
    graph_ = await merge_graph(db, user_id, readonly=True)
    games = graph_.group(game_id)
    data = await _load(db, user_id, games)
    metrics: dict[str, Any] = {}
    for metric in METRIC_FACTS:
        views = [_counter_view(d) for d in data if d.counter.metric == metric]
        metrics[metric] = {"counters": views, **_total(metric, views)}
    tracked = await manual.achievements_view(db, user_id, game_id)
    if tracked["umbrella"]["total"]:
        found = tracked["umbrella"]
        metrics[GameMetric.ACHIEVEMENTS.value].update(
            effective=found["unlocked"],
            unlocked=found["unlocked"],
            total=found["total"],
            exact=True,
            basis="achievement rows and manual achievements",
            versions=tracked["versions"],
        )
    # the same answer whichever member of the group it is asked from
    tracked_completion = await manual.completion_view(db, user_id, game_id)
    tracked_completion.pop("game_id")
    metrics[GameMetric.COMPLETION.value]["tracked"] = tracked_completion
    metrics[GameMetric.COMPLETION.value]["user_value"] = await _field(
        db, user_id, games, "completion_status"
    )
    return {
        "game_id": game_id,
        "identity_id": graph_.root(game_id),
        "games": sorted(games, key=str),
        "metrics": metrics,
        "rating": await _field(db, user_id, games, "rating"),
    }
