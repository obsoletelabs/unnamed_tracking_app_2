"""Anime franchise graphs: one fetch/store path for titles and the scheduled franchise check.

Opening a title and the scheduled check store the same graph through the metadata gateway,
so every stored graph is also the baseline for announcing newly listed entries.

The scheduled check visits opted-in accounts in id order and each account's completed
franchises in identity order. A run checks a small bounded batch and persists its cursor
in the job's run result, so restarts resume rather than starve later accounts. Titles
sharing a stored sequence are one franchise and cost one provider fetch. A provider
failure stops the batch and backs off; a franchise that keeps failing is skipped until
the next pass, so it cannot block everyone else."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.anime import Anime
from src.database.models.notification import Notification
from src.database.models.user import User
from src.database.models.user_preferences import UserPreferences
from src.database.session import SessionLocal
from src.features.metadata.anime.franchise import RELATIONS_CACHE_VERSION
from src.features.metadata.service import library_candidate, related_metadata, relation_result
from src.features.notification_controller import EVENT_TYPES
from src.features.notification_policy import preference_enabled
from src.features.notifications import (
    COMPLETED_ANIME,
    anilist_entry_id,
    franchise_entry_ids,
    record_franchise_changes,
)
from src.plugin_api.metadata_contracts import MediaType

logger = logging.getLogger(__name__)

FRANCHISE_KINDS = ("franchise_season", "franchise_extra")
KNOWN_IDS_LIMIT = 1000
MAX_FRANCHISES_PER_RUN = 5
CONTINUE_AFTER_SECONDS = 5 * 60
MAX_BACKOFF_SECONDS = 60 * 60
STALL_LIMIT = 3
CHECK_TIMEOUT_SECONDS = 120
relations_inflight: set[UUID] = set()


async def fetch_and_store_relations(
    db: AsyncSession, show: Anime, *, scheduled: bool = False
) -> tuple[dict[str, Any], int]:
    """Ask the provider gateway (slow: a paced request per franchise entry), store the graph
    and announce newly listed entries in the same transaction. Returns (graph, created)."""
    old_cache = show.relations_cache
    ids = {
        **(show.provider_ids or {}),
        **({"anilist": show.anilist_id} if show.anilist_id else {}),
        **({"mal": show.external_id} if show.external_id else {}),
    }
    data = await related_metadata(
        db, show.user_id, library_candidate(show.title, MediaType.ANIME, ids)
    )
    result: dict[str, Any] = {
        "chain": [],
        "branches": [],
        "recommendations": [],
        "version": RELATIONS_CACHE_VERSION,
        "configured": data["configured"],
    }
    for relation in data["relations"]:
        group = {"chain": "chain", "branch": "branches", "recommendation": "recommendations"}.get(
            relation.get("group"), "branches"
        )
        result[group].append(relation_result(relation))
    if not data["configured"]:
        return result, 0
    # Entries an earlier fetch listed stay known when a later graph is partial.
    known = franchise_entry_ids(old_cache) | franchise_entry_ids(result)
    stored = {**result, "known_ids": sorted(known)[-KNOWN_IDS_LIMIT:]}
    created, deferred = await record_franchise_changes(
        db, show, old_cache, result, scheduled=scheduled
    )
    if not deferred:
        show.relations_cache = stored
        show.relations_cached_at = int(time.time())
    await db.commit()
    return stored, created


async def refresh_relations_in_background(show_id: UUID) -> None:
    """Fetch and store a franchise graph without making anyone wait for it. Used for a stale
    cache (the old one is served meanwhile) and to warm a title right after it is added."""
    if show_id in relations_inflight:
        return
    relations_inflight.add(show_id)
    try:
        async with SessionLocal() as db:
            show = await db.scalar(select(Anime).where(Anime.id == show_id))
            if show is None or not show.anilist_id:
                return
            await fetch_and_store_relations(db, show)
    # This background task reports its own failure and always releases the inflight marker.
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Background franchise refresh failed for %s", show_id)
    finally:
        relations_inflight.discard(show_id)


@dataclass(frozen=True)
class FranchiseGroup:
    """One franchise in an account's library: its identity and the title fetched for it."""

    key: int
    anime_id: UUID


@dataclass(frozen=True)
class _Title:
    own: int
    sequence: list[int]
    cached_at: int | None
    anime_id: UUID


def _title(row: Any) -> _Title | None:
    """A completed title's own AniList id and its stored sequence, if any."""
    own = anilist_entry_id(row.anilist_id)
    if own is None:
        return None
    stored = row.chain if isinstance(row.chain, list) else []
    sequence = [anilist_entry_id(node.get("id")) for node in stored if isinstance(node, dict)]
    return _Title(own, [entry for entry in sequence if entry], row.relations_cached_at, row.id)


def _franchise_roots(titles: list[_Title]) -> dict[int, int]:
    """Union titles sharing any sequence entry; maps each title's own id to its franchise."""
    parent: dict[int, int] = {}

    def root(entry_id: int) -> int:
        while parent.setdefault(entry_id, entry_id) != entry_id:
            parent[entry_id] = parent[parent[entry_id]]
            entry_id = parent[entry_id]
        return entry_id

    for title in titles:
        for entry_id in title.sequence:
            parent[root(entry_id)] = root(title.own)
    return {title.own: root(title.own) for title in titles}


def _representative(titles: list[_Title]) -> _Title:
    """The title latest in the franchise's stored sequence; among equals, the oldest graph."""
    order: dict[int, int] = {}
    for title in titles:
        for index, entry_id in enumerate(title.sequence):
            order[entry_id] = max(order.get(entry_id, -1), index)
    return min(
        titles,
        key=lambda title: (
            -order.get(title.own, -1),
            title.cached_at is not None,
            title.cached_at or 0,
            str(title.anime_id),
        ),
    )


async def franchise_groups(db: AsyncSession, user_id: UUID) -> list[FranchiseGroup]:
    """Unite the owner's completed titles whose stored sequences share an AniList entry.

    The identity is the lowest AniList id in the franchise, which a newly listed sequel
    cannot change. The completed title latest in the sequence is fetched, because a graph
    lists side stories of the fetched title."""
    rows = (
        await db.execute(
            select(
                Anime.id,
                Anime.anilist_id,
                Anime.relations_cached_at,
                Anime.relations_cache["chain"].label("chain"),
            ).where(
                Anime.user_id == user_id,
                Anime.deleted_at.is_(None),
                Anime.status.in_(COMPLETED_ANIME),
                Anime.anilist_id.is_not(None),
            )
        )
    ).all()
    titles = [title for title in map(_title, rows) if title is not None]
    roots = _franchise_roots(titles)
    franchises: dict[int, list[_Title]] = {}
    for title in titles:
        franchises.setdefault(roots[title.own], []).append(title)
    groups = [
        FranchiseGroup(
            key=min(entry for title in members for entry in [title.own, *title.sequence]),
            anime_id=_representative(members).anime_id,
        )
        for members in franchises.values()
    ]
    return sorted(groups, key=lambda group: group.key)


def franchise_checks_enabled(prefs: dict[str, Any]) -> bool:
    """An account joins the scheduled check only after opting in to at least one type."""
    return "anime" in prefs["notify_media_types"] and any(
        preference_enabled(Notification(kind=kind, event_type=EVENT_TYPES[kind]), prefs)
        for kind in FRANCHISE_KINDS
    )


async def _opted_in_accounts(after: str, *, inclusive: bool) -> list[UUID]:
    data = UserPreferences.data
    statement = (
        select(UserPreferences.user_id)
        .join(User, User.id == UserPreferences.user_id)
        .where(
            User.is_active.is_(True),
            or_(*(data[f"notify_{kind}"].as_boolean().is_(True) for kind in FRANCHISE_KINDS)),
        )
        .order_by(UserPreferences.user_id)
    )
    if after:
        cursor = UUID(after)
        statement = statement.where(
            UserPreferences.user_id >= cursor if inclusive else UserPreferences.user_id > cursor
        )
    async with SessionLocal() as db:
        return list(await db.scalars(statement))


async def _check_franchise(anime_id: UUID) -> int:
    """Refresh one franchise through its fetched title; an opened title already doing so wins."""
    if anime_id in relations_inflight:
        return 0
    relations_inflight.add(anime_id)
    try:
        async with SessionLocal() as db:
            show = await db.scalar(
                select(Anime).where(Anime.id == anime_id, Anime.deleted_at.is_(None))
            )
            if show is None or not show.anilist_id:
                return 0
            _graph, created = await fetch_and_store_relations(db, show, scheduled=True)
            return created
    finally:
        relations_inflight.discard(anime_id)


async def _account_groups(user_id: UUID, after: int) -> list[FranchiseGroup] | None:
    """The account's franchises after the cursor, or None when it has not opted in."""
    async with SessionLocal() as db:
        if not franchise_checks_enabled(await load_preferences(db, user_id)):
            return None
        return [group for group in await franchise_groups(db, user_id) if group.key > after]


def _failure_code(exc: Exception) -> str:
    """A safe classification for the run report, never a remote body."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "unavailable" if isinstance(exc, HTTPException) else "failed"


def _cursor(previous: dict[str, Any]) -> tuple[str, int, int]:
    """Resume an unfinished pass; anything else starts a new pass at the first account."""
    if previous.get("completed") is not False:
        return "", 0, 0
    user, key, stalled = (
        previous.get("cursor_user"),
        previous.get("cursor_franchise"),
        previous.get("stalled"),
    )
    try:
        user = str(UUID(str(user))) if user else ""
    except ValueError:
        return "", 0, 0
    key = key if isinstance(key, int) and not isinstance(key, bool) else 0
    stalled = stalled if isinstance(stalled, int) and not isinstance(stalled, bool) else 0
    return user, key, max(0, stalled)


def _summary(result: dict[str, Any]) -> str:
    text = (
        f"{result['checked']} franchise{'s' if result['checked'] != 1 else ''} checked, "
        f"{result['notified']} new listing{'s' if result['notified'] != 1 else ''}"
    )
    if result["failed"]:
        text += f", {result['failed']} unavailable"
    if result["skipped"]:
        text += f", {result['skipped']} skipped until the next pass"
    if result.get("continue_after_seconds"):
        minutes = max(1, result["continue_after_seconds"] // 60)
        text += f". Continues in {minutes} minute{'s' if minutes != 1 else ''}."
    elif not result["accounts"] and not result["checked"]:
        text = "No accounts have opted in to anime franchise checks."
    else:
        text += ". Every opted-in account was checked."
    return text


async def run_franchise_check(previous: dict[str, Any]) -> dict[str, Any]:
    """Check one bounded batch of opted-in accounts' completed franchises."""
    cursor_user, cursor_key, stalled = _cursor(previous)
    result: dict[str, Any] = {
        "completed": False,
        "accounts": 0,
        "checked": 0,
        "notified": 0,
        "failed": 0,
        "skipped": 0,
        "error": "",
    }

    def pause(user_id: str, key: int, delay: int) -> dict[str, Any]:
        result.update(
            cursor_user=user_id,
            cursor_franchise=key,
            stalled=stalled,
            continue_after_seconds=delay,
        )
        result["summary"] = _summary(result)
        return result

    budget = MAX_FRANCHISES_PER_RUN
    for user_id in await _opted_in_accounts(cursor_user, inclusive=cursor_key >= 0):
        position = cursor_key if str(user_id) == cursor_user else 0
        visited = False
        while True:
            try:
                # Recomputed after every fetch: a newly stored graph can unite titles
                # that looked separate, so the same franchise is not fetched twice.
                groups = await _account_groups(user_id, position)
            # One unreadable account must not block the accounts after it.
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Franchise check could not read account %s", user_id)
                result["failed"] += 1
                result["error"] = "failed"
                break
            if groups is None:
                break
            result["accounts"] += 0 if visited else 1
            visited = True
            if not groups:
                break
            if budget == 0:
                return pause(str(user_id), position, CONTINUE_AFTER_SECONDS)
            budget -= 1
            try:
                created = await asyncio.wait_for(
                    _check_franchise(groups[0].anime_id), CHECK_TIMEOUT_SECONDS
                )
            # Provider, timeout and storage failures are isolated to this franchise.
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Franchise check failed for anime %s", groups[0].anime_id)
                result["failed"] += 1
                result["error"] = _failure_code(exc)
                # Consecutive failures back off up to hourly. Each franchise gets
                # STALL_LIMIT attempts, then waits for the next pass.
                stalled += 1
                if stalled % STALL_LIMIT:
                    return pause(
                        str(user_id),
                        position,
                        min(MAX_BACKOFF_SECONDS, CONTINUE_AFTER_SECONDS * 2 ** (stalled - 1)),
                    )
                result["skipped"] += 1
            else:
                result["checked"] += 1
                result["notified"] += created
                stalled = 0
            position = groups[0].key
    result["completed"] = True
    result["summary"] = _summary(result)
    return result
