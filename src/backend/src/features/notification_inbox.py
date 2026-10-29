"""Owner-scoped inbox queries and effective retention, independent of transport."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_story import NotificationStory, NotificationStoryRevision
from src.features.notification_lifecycle import visible_inbox
from src.features.notification_policy import INBOX_PROVIDER

CATEGORY_KINDS = {
    "episodes": ("episode_aired",),
    "seasons": ("season_started", "sequel_announced", "franchise_season"),
    "releases": (
        "movie_released",
        "franchise_extra",
        "game_released",
        "game_sale",
        "game_price_hit",
    ),
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
    # Which lifecycle slice to browse; "active" is the unchanged inbox and bell view.
    state: Literal["active", "dismissed", "expired", "deleted", "history"] = "active"
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    include_summary: bool = True


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


def _was_in_inbox():
    """The item reached the in-app inbox: an item the user routed away from it (muted or
    disabled for the inbox) has no inbox delivery and must not appear as expired history."""
    return exists().where(
        NotificationDelivery.notification_id == Notification.id,
        NotificationDelivery.provider_id == INBOX_PROVIDER,
    )


def state_scope(user_id: UUID, state: str):
    """Owner-scoped predicate for one lifecycle slice. Dismissal hides, never erases."""
    owned = Notification.user_id == user_id
    if state == "dismissed":
        return (
            owned
            & Notification.inbox_visible.is_(True)
            & Notification.dismissed_at.is_not(None)
            & Notification.deleted_at.is_(None)
        )
    if state == "expired":
        return (
            owned
            & Notification.inbox_visible.is_(False)
            & Notification.deleted_at.is_(None)
            & _was_in_inbox()
        )
    if state == "deleted":
        return owned & Notification.deleted_at.is_not(None)
    if state == "history":
        return owned & (Notification.inbox_visible.is_(True) | _was_in_inbox())
    return visible_inbox(user_id)


def lifecycle_state(n: Notification, now: int) -> str:
    """Same precedence as the story item state: deleted, expired, dismissed, read, unread."""
    if n.deleted_at is not None:
        return "deleted"
    if not n.inbox_visible or (n.expires_at is not None and n.expires_at <= now):
        return "expired"
    if n.dismissed_at is not None:
        return "dismissed"
    return "read" if n.read_at is not None else "unread"


async def story_details(db: AsyncSession, rows: list[Notification]) -> dict[UUID, dict[str, Any]]:
    """Phase and revision count per story, read for the page's owner-scoped rows only."""
    story_ids = {n.story_id for n in rows if n.story_id}
    if not story_ids:
        return {}
    phases = {
        story.id: story
        for story in await db.scalars(
            select(NotificationStory).where(NotificationStory.id.in_(story_ids))
        )
    }
    revision_rows = await db.execute(
        select(NotificationStoryRevision.story_id, sql_count())
        .where(NotificationStoryRevision.story_id.in_(story_ids))
        .group_by(NotificationStoryRevision.story_id)
    )
    revisions = dict(revision_rows.tuples().all())
    return {
        story_id: {
            "phase": story.phase,
            "resolved": story.phase == "resolved",
            "revisions": revisions.get(story_id, 1),
        }
        for story_id, story in phases.items()
    }


async def state_counts(db: AsyncSession, user_id: UUID) -> dict[str, int]:
    """Per-slice totals for the status selector; always the owner's, never the page's."""
    states: dict[str, int] = {}
    for name in ("active", "dismissed", "expired", "deleted", "history"):
        count = await db.scalar(
            select(sql_count()).select_from(Notification).where(state_scope(user_id, name))
        )
        states[name] = count or 0
    return states


async def count_unread(db: AsyncSession, user_id: UUID) -> int:
    """Read the persisted badge count without discovery or inbox summaries."""
    return (
        await db.scalar(
            select(sql_count())
            .select_from(Notification)
            .where(visible_inbox(user_id), Notification.read_at.is_(None))
        )
        or 0
    )


async def query_inbox(
    db: AsyncSession, user_id: UUID, query: InboxQuery
) -> tuple[list[Notification], dict[str, Any]]:
    """Counts cover the matching inbox, never just the current page."""
    predicates = [state_scope(user_id, query.state)]
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
    if not query.include_summary:
        if query.unread_only or query.category == "unread":
            predicates.append(Notification.read_at.is_(None))
        if query.category in CATEGORY_KINDS:
            predicates.append(Notification.kind.in_(CATEGORY_KINDS[query.category]))
        rows = list(
            await db.scalars(
                select(Notification)
                .where(*predicates)
                .order_by(Notification.event_at.desc(), Notification.id.desc())
                .limit(query.limit + 1)
                .offset(query.offset)
            )
        )
        return rows[: query.limit], {
            "unread": await count_unread(db, user_id),
            "total": None,
            "next_offset": query.offset + query.limit if len(rows) > query.limit else None,
            "counts": {},
            "states": {},
            "sources": [],
        }
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
    states = await state_counts(db, user_id)
    return rows, {
        "states": states,
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
