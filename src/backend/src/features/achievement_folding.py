"""Matching provider catalogues to what a player earned, and merging the copies of a game.

A catalogue row (``provider == "catalog"``) says an achievement exists. It never carries
progress: the platform syncs (Steam, PlayStation, RetroAchievements) and the user own that.
A catalogue row is matched to a synced row through the ids the provider reports for it, and
only lends the synced row the details it lacks (icons, rarity, points, kind).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from src.database.models.achievement import Achievement

CATALOG = "catalog"
HIDDEN_NAME = "Hidden achievement"


def is_catalog(row: Achievement) -> bool:
    return row.provider == CATALOG


def fold_catalog(
    rows: Iterable[Achievement],
) -> list[tuple[Achievement, Achievement | None]]:
    """Pair each row with the catalogue row describing it, if any.

    Catalogue rows that match no synced row stay as entries of their own (locked, with no
    progress). Matching is by the platform ids the catalogue reports; a catalogue row is
    used for at most one synced row per game version, so a duplicate is never counted."""
    synced: list[Achievement] = []
    catalog: list[Achievement] = []
    for row in rows:
        (catalog if is_catalog(row) else synced).append(row)
    index = {(row.provider.casefold(), row.external_id): row for row in synced}
    claimed: dict[UUID, Achievement] = {}
    used: set[UUID] = set()
    for entry in catalog:
        for platform, external_id in (entry.source_ids or {}).items():
            match = index.get((platform.casefold(), external_id))
            if match is not None and match.id not in claimed:
                claimed[match.id] = entry
                used.add(entry.id)
                break
    pairs: list[tuple[Achievement, Achievement | None]] = [
        (row, claimed.get(row.id)) for row in synced
    ]
    pairs.extend((entry, None) for entry in catalog if entry.id not in used)
    return pairs


def _icon_path(owner: Achievement, url: str | None, locked: bool = False) -> str | None:
    """Remote icons are served from this server's cache; anything else is passed through."""
    if url and url.startswith(("http://", "https://")):
        return f"/api/achievement-icon/{owner.id}" + ("/locked" if locked else "")
    return url


def lend(row: Achievement, entry: Achievement | None) -> dict[str, Any]:
    """The details a synced row shows, with gaps filled from its catalogue row.

    Progress (unlocked, unlocked_at) always comes from ``row``. A hidden catalogue placeholder
    lends no text: the synced row already has the real text."""
    icon_owner, icon = (row, row.icon_url)
    if icon is None and entry is not None:
        icon_owner, icon = entry, entry.icon_url
    locked_owner, locked = (row, row.locked_icon_url)
    if locked is None and entry is not None:
        locked_owner, locked = entry, entry.locked_icon_url
    details: dict[str, Any] = {
        "icon_url": _icon_path(icon_owner, icon),
        "locked_icon_url": _icon_path(locked_owner, locked, locked=True),
        "global_percent": row.global_percent,
        "tier": row.tier,
        "medal": row.medal,
        "points": row.points,
        "source_ids": row.source_ids,
        "hidden": bool(row.hidden),
        "description": row.description,
    }
    if entry is None:
        return details
    for field in ("global_percent", "tier", "medal", "points", "source_ids"):
        if details[field] is None:
            details[field] = getattr(entry, field)
    details["hidden"] = bool(row.hidden or entry.hidden)
    if not details["description"] and not entry.hidden:
        details["description"] = entry.description
    return details
