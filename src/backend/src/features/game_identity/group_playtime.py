"""Reconciled playtime of many identity groups at once (#277).

The library shows one total per merged group. This reads them in a constant number of
statements, using the same per-counter values `counters.reconcile` reports for the game page."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.features.game_identity.counters import _PLAYTIME, _counter_view, _load


async def playtime_by_group(
    db: AsyncSession, user_id: UUID, groups: dict[UUID, set[UUID]]
) -> dict[UUID, dict[UUID, int]]:
    """Effective playtime of many identity groups in a constant number of statements.

    Returns, per key of `groups`, the effective playtime of each game that holds a playtime
    counter. It is exactly the per-counter value `reconcile` reports (observations of one
    counter are counted once, adjustments applied); callers add the totals the same way.
    Read only: counters are not materialised here (see `counters.sync_many`)."""
    everyone = set().union(*groups.values()) if groups else set()
    if not everyone:
        return {key: {} for key in groups}
    data = [d for d in await _load(db, user_id, everyone) if d.counter.metric == _PLAYTIME]
    found: dict[UUID, int] = {}
    for item in data:
        effective = _counter_view(item)["effective"]
        if effective is not None:
            found[item.counter.game_id] = found.get(item.counter.game_id, 0) + int(effective)
    return {key: {g: found[g] for g in members if g in found} for key, members in groups.items()}
