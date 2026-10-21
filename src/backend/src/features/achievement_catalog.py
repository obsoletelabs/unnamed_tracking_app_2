"""Achievement lists reported by metadata providers, kept apart from what a player earned.

A catalogue row (``provider == "catalog"``) says an achievement exists. It never carries
progress: the platform syncs (Steam, PlayStation, RetroAchievements) and the user own that.
A catalogue row is matched to a synced row through the ids the provider reports for it, and
only lends the synced row the details it lacks (icons, rarity, points, kind).
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.achievement import Achievement

CATALOG = "catalog"
HIDDEN_NAME = "Hidden achievement"


def is_catalog(row: Achievement) -> bool:
    return row.provider == CATALOG


def catalog_values(item: dict[str, Any]) -> dict[str, Any]:
    """Column values for one validated ``MetadataAchievement`` as a plain dict."""
    return {
        "name": item["name"],
        "description": item.get("description"),
        "icon_url": item.get("icon_url"),
        "locked_icon_url": item.get("locked_icon_url"),
        "hidden": bool(item.get("hidden")),
        "tier": item.get("kind"),
        "medal": item.get("tier"),
        "points": item.get("points"),
        "global_percent": item.get("global_percent"),
        "source_ids": dict(item.get("source_ids") or {}) or None,
        "source_provider": str(item.get("provider") or "")[:128] or None,
    }


async def store_catalog(db: AsyncSession, game_id: UUID, items: Sequence[dict[str, Any]]) -> int:
    """Upsert the provider's list for this game and return how many rows changed.

    An empty list changes nothing: a provider that knows no achievements, or failed to say,
    must not wipe a list an earlier refresh stored. Rows the provider stopped listing go away,
    but only those a metadata provider wrote."""
    if not items:
        return 0
    rows = await db.scalars(
        select(Achievement).where(Achievement.game_id == game_id, Achievement.provider == CATALOG)
    )
    existing = {row.external_id: row for row in rows}
    changed = 0
    seen: set[str] = set()
    for item in items:
        external_id = item["external_id"]
        if external_id in seen:
            continue
        seen.add(external_id)
        values = catalog_values(item)
        row = existing.get(external_id)
        if row is None:
            db.add(
                Achievement(
                    game_id=game_id,
                    provider=CATALOG,
                    external_id=external_id,
                    created_at=int(time.time()),
                    unlocked=False,
                    **values,
                )
            )
            changed += 1
            continue
        dirty = False
        for field, value in values.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                dirty = True
        changed += dirty
    for external_id, row in existing.items():
        if external_id not in seen:
            await db.delete(row)
            changed += 1
    return changed


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


def _name_key(row: Achievement) -> str | None:
    name = " ".join(row.name.casefold().split())
    return None if not name or name == HIDDEN_NAME.casefold() else name


def merge_copies(
    copies: Sequence[tuple[Any, str]], rows: Iterable[Achievement]
) -> list[dict[str, Any]]:
    """One achievement list for a game page that shows several copies (#339).

    ``copies`` are ``(label, app_id)`` pairs, main copy first, where the label has ``game_id``,
    ``title``, ``source`` and ``platform`` and ``app_id`` is the copy's store id. The same
    achievement is listed once when it has the same platform id in the same store app, or the
    same name on another copy (a Steam and a GOG copy of one game). Each entry keeps the state
    every copy reports in ``sources``; it counts as unlocked when any source is, with the
    earliest unlock time. Entries from the main copy lead, so a note or screenshot linked to an
    achievement keeps pointing at the row the page shows."""
    by_game: dict[UUID, list[Achievement]] = {}
    for row in rows:
        by_game.setdefault(row.game_id, []).append(row)
    merged: list[dict[str, Any]] = []
    by_id: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for label, app_id in copies:
        for row, entry in fold_catalog(by_game.get(label.game_id, [])):
            item = _list_entry(row, entry)
            id_key = (row.provider.casefold(), app_id, row.external_id)
            name_key = _name_key(row)
            target = by_id.get(id_key) or (by_name.get(name_key) if name_key else None)
            source = _source_of(row, label)
            if target is None:
                item["sources"] = [source]
                merged.append(item)
                target = item
            else:
                _absorb(target, item, source)
            by_id.setdefault(id_key, target)
            if name_key:
                by_name.setdefault(name_key, target)
    return merged


def _list_entry(row: Achievement, entry: Achievement | None) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "provider": row.provider,
        "name": row.name,
        "unlocked": bool(row.unlocked),
        "unlocked_at": row.unlocked_at,
        **lend(row, entry),
    }


def _source_of(row: Achievement, label: Any) -> dict[str, Any]:
    return {
        "game_id": str(label.game_id),
        "copy": label.title,
        "store": label.source,
        "platform": label.platform,
        "provider": row.provider,
        "unlocked": bool(row.unlocked),
        "unlocked_at": row.unlocked_at,
    }


def _absorb(target: dict[str, Any], item: dict[str, Any], source: dict[str, Any]) -> None:
    target["sources"].append(source)
    if item["unlocked"]:
        times = [t for t in (target["unlocked_at"], item["unlocked_at"]) if t is not None]
        target["unlocked"] = True
        target["unlocked_at"] = min(times) if times else None
    for field in (
        "icon_url",
        "locked_icon_url",
        "global_percent",
        "tier",
        "medal",
        "points",
        "source_ids",
        "description",
    ):
        if target[field] in (None, ""):
            target[field] = item[field]
    # a secret only stays secret while no copy shows it openly or has it unlocked
    target["hidden"] = bool(target["hidden"] and item["hidden"]) and not target["unlocked"]
