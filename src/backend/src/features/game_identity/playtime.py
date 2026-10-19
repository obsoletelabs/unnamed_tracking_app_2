"""Effective playtime of reconciled counters, shared by the counter views and the overview."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.database.models.game_identity_tracking import GameMetric
from src.features.game_identity import policy
from src.features.game_identity.policy import Reading

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.features.game_identity.counters import _CounterData

PLAYTIME = GameMetric.PLAYTIME.value


def effective(item: _CounterData) -> tuple[Reading | None, Any, Any]:
    """The winning reading, its raw value and the effective value after any adjustment."""
    winner = item.winner
    raw = winner.value if winner else None
    value = raw
    if item.counter.metric == PLAYTIME and item.adjustment is not None:
        value = policy.apply_playtime_adjustment(
            raw if raw is None else int(raw),
            item.adjustment.kind,
            item.adjustment.offset_value,
            item.adjustment.override_value,
        )
    return winner, raw, value


def totals(items: Iterable[_CounterData]) -> dict[UUID, int]:
    """Playtime per game; independent counters of one game add up. A game without any is absent."""
    found: dict[UUID, int] = {}
    for item in items:
        value = effective(item)[2]
        if value is not None:
            found[item.counter.game_id] = found.get(item.counter.game_id, 0) + int(value)
    return found
