"""Resolve collection membership from one lightweight, account-owned index."""

from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.core.titles import ALT_FIELDS, display_title
from src.database.models.media_extras import MediaList, MediaListItem

_BUCKET_STATUSES = {
    "plan": {"WISHLIST", "WATCHLIST"},
    "hold": {"BACKLOG"},
    "watching": {"IN_PROGRESS", "REWATCH"},
    "completed": {"WATCHED", "FAVORITE"},
    "dropped": {"DROPPED"},
}
_FIELDS = (
    "id",
    "title",
    *ALT_FIELDS,
    "locked_fields",
    "poster_url",
    "status",
    "favorite",
    "genres",
    "rating_overall",
)


def item_dict(
    item_id: UUID, media_type: str, media: Any, added_at: int, language: str = "english"
) -> dict:
    return {
        "id": item_id,
        "media_type": str(getattr(media_type, "value", media_type)),
        "media_id": media.id,
        "title": display_title(media, language),
        "poster_url": media.poster_url,
        "status": media.status.value if hasattr(media.status, "value") else str(media.status),
        "added_at": added_at,
    }


def _matches(media: Any, rule: dict) -> bool:
    buckets = rule.get("status_buckets")
    if buckets:
        allowed = set().union(*(_BUCKET_STATUSES.get(bucket, set()) for bucket in buckets))
        status = media.status.value if hasattr(media.status, "value") else str(media.status)
        if status not in allowed:
            return False
    if rule.get("favorite") is not None and bool(media.favorite) != rule["favorite"]:
        return False
    genre = (rule.get("genre") or "").strip().lower()
    if genre and genre not in [value.lower() for value in (media.genres or [])]:
        return False
    score = rule.get("min_score")
    return score is None or (
        media.rating_overall is not None and float(media.rating_overall) >= score
    )


async def _index(
    lists: Sequence[MediaList],
    members: Sequence[MediaListItem],
    user_id: UUID,
    db: AsyncSession,
    models: Mapping[str, Any],
) -> dict[tuple[str, UUID], Any]:
    index = {}
    for kind, model in models.items():
        smart = any(
            lst.smart_rule is not None and kind in (lst.smart_rule.get("media_types") or models)
            for lst in lists
        )
        ids = {item.media_id for item in members if item.media_type == kind}
        if not smart and not ids:
            continue
        stmt = select(
            *(getattr(model, field, literal(None)).label(field) for field in _FIELDS)
        ).where(model.user_id == user_id, model.deleted_at.is_(None))
        if not smart:
            stmt = stmt.where(model.id.in_(ids))
        for row in (await db.execute(stmt)).mappings():
            media = SimpleNamespace(**row)
            index[kind, media.id] = media
    return index


async def resolve_items(
    lists: Sequence[MediaList],
    user_id: UUID,
    db: AsyncSession,
    models: Mapping[str, Any],
) -> dict[UUID, list[dict]]:
    """At most one membership and one projection query per media type, not per item/list."""
    language = str((await load_preferences(db, user_id))["title_language"])
    members = (
        await db.scalars(
            select(MediaListItem)
            .where(MediaListItem.list_id.in_([lst.id for lst in lists]))
            .order_by(
                MediaListItem.list_id, MediaListItem.position.asc(), MediaListItem.added_at.desc()
            )
        )
    ).all()
    index = await _index(lists, members, user_id, db, models)
    by_list: dict[UUID, list[dict]] = {lst.id: [] for lst in lists}
    for item in members:
        media = index.get((item.media_type, item.media_id))
        if media is not None:
            by_list[item.list_id].append(
                item_dict(item.id, item.media_type, media, item.added_at, language)
            )
    for lst in lists:
        if lst.smart_rule is None:
            continue
        kinds = lst.smart_rule.get("media_types") or list(models)
        by_list[lst.id] = [
            item_dict(media.id, kind, media, 0, language)
            for kind in kinds
            for (media_kind, _), media in index.items()
            if media_kind == kind and _matches(media, lst.smart_rule)
        ]
        by_list[lst.id].sort(key=lambda item: item["title"].lower())
    return by_list
