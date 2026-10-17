"""Pure per-metric reconciliation rules (#96). No database access.

A *counter* groups the readings of one thing that several sources report (Playnite
and a manual entry counting the same install). Within a counter exactly one reading
is effective; independent counters are then combined with the metric's own rule.
Every function is deterministic: ties are broken by priority, freshness, then the
observation ID, so the same inputs always give the same answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

DEFAULT_PRIORITY = 100
COMPLETED_STATUSES = frozenset({"completed", "beaten", "finished", "mastered", "100%"})


@dataclass(frozen=True)
class Reading:
    """One observation's report of one metric. `value` None means 'not reported'."""

    observation_id: UUID
    source: str
    value: Any
    observed_at: int | None
    priority: int = DEFAULT_PRIORITY
    detail: Any = None  # extra values of the same observation (achievement totals)
    available: bool = True


def _freshness(reading: Reading) -> int:
    return reading.observed_at if reading.observed_at is not None else -1


def pick_preferred(readings: Iterable[Reading]) -> Reading | None:
    """Lowest priority number wins, then the freshest report of *this metric*.

    Freshness is the per-field time of the metric's own fact, never the record's
    last update, so a newer name from one source cannot make it win playtime."""
    reported = [r for r in readings if r.value is not None]
    if not reported:
        return None
    return min(reported, key=lambda r: (r.priority, -_freshness(r), str(r.observation_id)))


def pick_latest(readings: Iterable[Reading]) -> Reading | None:
    """Largest value wins (last played); priority and the ID only break ties."""
    reported = [r for r in readings if r.value is not None]
    if not reported:
        return None
    return min(reported, key=lambda r: (-r.value, r.priority, str(r.observation_id)))


def losers(readings: Sequence[Reading], winner: Reading | None) -> list[dict[str, Any]]:
    """Why each non-effective reading did not win, for the conflict provenance."""
    out: list[dict[str, Any]] = []
    for reading in sorted(readings, key=lambda r: str(r.observation_id)):
        if winner is None or reading is winner or reading.value is None:
            continue
        if reading.priority != winner.priority:
            why = "lower source priority"
        elif _freshness(reading) != _freshness(winner):
            why = "older report of this metric"
        else:
            why = "tie broken by observation ID"
        out.append(
            {
                "observation_id": reading.observation_id,
                "source": reading.source,
                "value": reading.value,
                "observed_at": reading.observed_at,
                "priority": reading.priority,
                "differs": reading.value != winner.value,
                "reason": why,
            }
        )
    return out


def apply_playtime_adjustment(
    base: int | None,
    kind: str | None,
    offset: int | None,
    override: Any,
) -> int | None:
    """Effective playtime = base + stored offset (never below zero), or the override.

    An unknown base never becomes 0: with an offset and no base the result stays
    unknown until a source reports again."""
    if kind == "override" and isinstance(override, int) and not isinstance(override, bool):
        return max(0, override)
    if base is None:
        return None
    if kind == "offset" and offset is not None:
        return max(0, base + offset)
    return base


def offset_for(desired: int, base: int) -> int:
    """The offset that makes `base` read as `desired` (200h -> 190h is -10h)."""
    return desired - base


def combine_any_true(values: Iterable[bool | None]) -> bool | None:
    """Ownership union: any report of True wins; False only if nothing says True."""
    seen = [v for v in values if v is not None]
    if not seen:
        return None
    return any(seen)


def is_completed(status: Any) -> bool:
    return isinstance(status, str) and status.strip().lower() in COMPLETED_STATUSES


def achievement_union(rows: Iterable[tuple[str, bool]]) -> tuple[int, int]:
    """Union of achievement rows by normalised name: (total, unlocked).

    The same achievement reported by two platforms counts once and is unlocked
    if any platform unlocked it."""
    merged: dict[str, bool] = {}
    for name, unlocked in rows:
        key = " ".join(name.casefold().split())
        merged[key] = merged.get(key, False) or bool(unlocked)
    return len(merged), sum(1 for flag in merged.values() if flag)
