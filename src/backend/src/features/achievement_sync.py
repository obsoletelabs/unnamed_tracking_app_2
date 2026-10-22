"""Shared row conversion and persistence helpers for achievement imports."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.achievement import Achievement


def _unix_from_text(text: object) -> int | None:
    """A provider's timestamp text ("2023-05-02 14:03:00" or an ISO string
    ending in Z) as unix seconds, or None when it's missing or unreadable."""
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def retro_achievement_rows(progress: dict) -> list[dict]:
    """Stored rows for one RetroAchievements game. `NumAwarded` over the
    game's player count is the share of players who earned each one."""
    players = progress.get("NumDistinctPlayers") or progress.get("NumDistinctPlayersCasual")
    rows = []
    for ach_id, ach in (progress.get("Achievements") or {}).items():
        earned = ach.get("DateEarned") or ach.get("DateEarnedHardcore")
        awarded = ach.get("NumAwarded")
        row = {
            "external_id": str(ach_id),
            "name": ach.get("Title") or str(ach_id),
            "description": ach.get("Description"),
            "icon_url": f"https://media.retroachievements.org/Badge/{ach['BadgeName']}.png"
            if ach.get("BadgeName")
            else None,
            "unlocked": bool(earned),
            "unlocked_at": _unix_from_text(earned),
            # RA's classic API's exact casing for this field isn't documented
            # as clearly as the newer v1 API's: check both
            "tier": (ach.get("Type") or ach.get("type") or "").lower() or None,
        }
        if isinstance(awarded, int | float) and isinstance(players, int | float) and players > 0:
            row["global_percent"] = round(awarded / players * 100, 2)
        rows.append(row)
    return rows


def psn_trophy_rows(trophies: list[dict]) -> list[dict]:
    """Stored rows for one PlayStation title: earned state and time, whether
    it's a hidden trophy, and the rate of players who earned it."""
    rows = []
    for t in trophies:
        if t.get("trophyId") is None:
            continue
        row = {
            "external_id": str(t.get("trophyId")),
            "name": t.get("trophyName") or "Trophy",
            "description": t.get("trophyDetail"),
            "icon_url": t.get("trophyIconUrl"),
            "unlocked": bool(t.get("earned")),
            "unlocked_at": _unix_from_text(t.get("earnedDateTime")),
            "hidden": bool(t.get("trophyHidden")),
        }
        try:
            row["global_percent"] = round(float(t["trophyEarnedRate"]), 2)
        except (KeyError, TypeError, ValueError):
            pass
        rows.append(row)
    return rows


async def replace_achievements(
    db: AsyncSession, game_id: UUID, provider: str, rows: list[dict]
) -> None:
    """Upserts by (game_id, provider, external_id) rather than delete +
    reinsert — a media item can link to a specific achievement
    (MediaItem.linked_achievement_id), and that FK is ON DELETE SET NULL,
    so wiping every row on each re-sync was silently unlinking every
    screenshot/clip tagged to an achievement the next time the game synced."""
    existing = {
        a.external_id: a
        for a in (
            await db.execute(
                select(Achievement).where(
                    Achievement.game_id == game_id, Achievement.provider == provider
                )
            )
        )
        .scalars()
        .all()
    }
    now = int(time.time())
    seen_ids: set[str] = set()
    for row in rows:
        seen_ids.add(row["external_id"])
        current = existing.get(row["external_id"])
        if current is not None:
            for field, value in row.items():
                # a lookup that found no description (Steam refused the extra
                # request, say) must not erase one we already have
                if field == "description" and value is None and current.description:
                    continue
                setattr(current, field, value)
        else:
            db.add(Achievement(game_id=game_id, provider=provider, created_at=now, **row))
    for external_id, achievement in existing.items():
        if external_id not in seen_ids:
            await db.delete(achievement)
