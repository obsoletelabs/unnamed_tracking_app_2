"""Owner-scoped inbox queries and effective retention, independent of transport."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.core.config import settings
from src.database.models.notification import Notification
from src.features.notification_lifecycle import visible_inbox

CATEGORY_KINDS = {
    "episodes": ("episode_aired",),
    "seasons": ("season_started", "sequel_announced"),
    "releases": ("movie_released", "game_released", "game_sale", "game_price_hit"),
    "security": ("session_anomaly",),
    "plugins": ("plugin", "plugin_update"),
}


class InboxQuery(BaseModel):
    category: Literal["all", "unread", "episodes", "seasons", "releases", "security", "plugins"] = (
        "all"
    )
    search: str = Field(default="", max_length=200)
    source: str = Field(default="", max_length=128)
    severity: Literal["", "info", "warning", "error"] = ""
    unread_only: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


def retention_policy(preferences: dict[str, Any]) -> dict[str, Any]:
    """Keep a user's requested duration even when a deployment maximum caps it."""
    inherited = preferences.get("notification_retention_inherit", True)
    requested = (
        settings.NOTIFICATION_RETENTION_DEFAULT_DAYS
        if inherited
        else int(preferences["notification_retention_days"])
    )
    maximum = settings.NOTIFICATION_RETENTION_MAXIMUM_DAYS
    limited = bool(maximum and (not requested or requested > maximum))
    return {
        "default_days": settings.NOTIFICATION_RETENTION_DEFAULT_DAYS,
        "maximum_days": maximum,
        "requested_days": requested,
        "effective_days": maximum if limited else requested,
        "inherited": inherited,
        "limited": limited,
    }


async def query_inbox(
    db: AsyncSession, user_id: UUID, query: InboxQuery
) -> tuple[list[Notification], dict[str, Any]]:
    """Counts cover the matching inbox, never just the current page."""
    predicates = [visible_inbox(user_id)]
    if query.search.strip():
        search = query.search.strip()
        predicates.append(
            Notification.title.icontains(search, autoescape=True)
            | Notification.body.icontains(search, autoescape=True)
        )
    if query.source:
        predicates.append(Notification.source == query.source)
    if query.severity:
        predicates.append(Notification.severity == query.severity)
    grouped_counts = await db.execute(
        select(Notification.kind, Notification.read_at.is_(None), sql_count())
        .where(*predicates)
        .group_by(Notification.kind, Notification.read_at.is_(None))
    )
    counts = dict.fromkeys(("all", "unread", *CATEGORY_KINDS), 0)
    for kind, unread, count in grouped_counts:
        counts["all"] += count
        counts["unread"] += count if unread else 0
        for category, kinds in CATEGORY_KINDS.items():
            if kind in kinds:
                counts[category] += count
    if query.unread_only or query.category == "unread":
        predicates.append(Notification.read_at.is_(None))
    if query.category in CATEGORY_KINDS:
        predicates.append(Notification.kind.in_(CATEGORY_KINDS[query.category]))
    total = await db.scalar(select(sql_count()).select_from(Notification).where(*predicates)) or 0
    rows = list(
        await db.scalars(
            select(Notification)
            .where(*predicates)
            .order_by(Notification.event_at.desc(), Notification.id.desc())
            .limit(query.limit)
            .offset(query.offset)
        )
    )
    unread = await db.scalar(
        select(sql_count())
        .select_from(Notification)
        .where(visible_inbox(user_id), Notification.read_at.is_(None))
    )
    return rows, {
        "total": total,
        "unread": unread or 0,
        "counts": counts,
        "next_offset": query.offset + len(rows) if query.offset + len(rows) < total else None,
        "sources": list(
            await db.scalars(
                select(Notification.source)
                .where(visible_inbox(user_id))
                .distinct()
                .order_by(Notification.source)
                .limit(256)
            )
        ),
    }
