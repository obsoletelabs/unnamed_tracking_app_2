"""Achievement definitions from metadata providers, stored beside synced ones without progress."""

# pylint: disable=not-callable

import time
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.achievement import Achievement
from src.database.models.game import Game
from src.features.metadata.service import collect_record, game_candidate

MAX_ROWS = 2000
PROVIDER_LABEL_LENGTH = 30


def catalogue_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows for the achievements table from a provider record; never unlocked, never trusted twice."""
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for item in (record.get("metadata") or {}).get("achievements") or []:
        external_id = item.get("external_id")
        if not isinstance(external_id, str) or external_id in seen:
            continue
        seen.add(external_id)
        rows.append(
            {
                "external_id": external_id[:200],
                "name": item["name"],
                "description": item.get("description"),
                "icon_url": item.get("icon_url"),
                "hidden": bool(item.get("hidden")),
                "global_percent": item.get("global_percent"),
                "unlocked": False,
            }
        )
    return rows[:MAX_ROWS]


def provider_label(record: dict[str, Any]) -> str:
    name = str(record.get("provider_name") or "Metadata providers").strip()
    return name[:PROVIDER_LABEL_LENGTH] or "Metadata providers"


async def fetch_catalogue(
    db: AsyncSession, user_id: UUID, game: Game
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Ask the user's metadata providers for this game's achievement definitions."""
    record, errors = await collect_record(
        db, user_id, game_candidate(game), resource="achievements"
    )
    return provider_label(record), catalogue_rows(record), errors


async def has_achievements(db: AsyncSession, game_id: UUID) -> bool:
    count = await db.scalar(
        select(func.count()).select_from(Achievement).where(Achievement.game_id == game_id)
    )
    return bool(count)


async def store_catalogue(
    db: AsyncSession, game_id: UUID, provider: str, rows: list[dict[str, Any]]
) -> None:
    """Upsert by external id, so a media item linked to an achievement keeps its link.

    Definitions the provider no longer lists are removed; unlock state is never set here."""
    existing = {
        a.external_id: a
        for a in (
            await db.scalars(
                select(Achievement).where(
                    Achievement.game_id == game_id, Achievement.provider == provider
                )
            )
        ).all()
    }
    now = int(time.time())
    for row in rows:
        current = existing.get(row["external_id"])
        if current is None:
            db.add(Achievement(game_id=game_id, provider=provider, created_at=now, **row))
            continue
        for field in ("name", "description", "icon_url", "hidden", "global_percent"):
            if row[field] is not None or field in ("hidden",):
                setattr(current, field, row[field])
    wanted = {row["external_id"] for row in rows}
    for external_id, achievement in existing.items():
        if external_id not in wanted:
            await db.delete(achievement)
