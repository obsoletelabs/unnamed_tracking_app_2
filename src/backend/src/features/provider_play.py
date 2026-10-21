"""Group-play details reported by metadata providers, kept apart from user-confirmed ones."""

from typing import Any, TypeGuard

PROVIDER_SOURCE = "provider"


def _is_count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def provider_play_compatibility(
    match: dict[str, Any], current: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The play_compatibility a refresh should store, or None to leave the game unchanged.

    Provider values are only used when both limits are known, and never replace details the user
    confirmed (those carry no ``source``). Crossplay is stored as true only when a provider says
    "yes": "no", "partial" and silence all stay false because the stored flag cannot express them.
    """
    low, high = match.get("players_min"), match.get("players_max")
    if not (_is_count(low) and _is_count(high)) or not 1 <= low <= high <= 32:
        return None
    if current and current.get("source") != PROVIDER_SOURCE:
        return None
    value: dict[str, Any] = {
        "min_players": low,
        "max_players": high,
        "crossplay": match.get("crossplay") == "yes",
        "source": PROVIDER_SOURCE,
    }
    if current and current.get("duration_minutes") is not None:
        value["duration_minutes"] = current["duration_minutes"]
    return value if value != current else None
