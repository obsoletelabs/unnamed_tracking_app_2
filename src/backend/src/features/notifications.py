"""Builds notifications from real, exact data. Nothing here estimates: an
episode notification exists only because a provider gave that episode an
air time (or the show's confirmed next-episode time) that has now passed,
and its `event_at` is that exact time.

Generation is shared by inbox polling and bounded scans in the existing
jobs loop. Durable receipts make discovery safe after content deletion."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason, AnimeStatus
from src.database.models.movies import Movie, MovieStatus
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow, TVShowStatus
from src.features.game_notifications import generate_game_releases
from src.features.notification_audit import change_notices
from src.features.notification_controller import EVENT_TYPES, emit_legacy_rows
from src.features.notification_inbox import retention_policy
from src.features.notification_policy import preference_enabled

# How far back "just aired" reaches. A week covers someone away for a few
# days without turning first use into a flood of old episodes.
WINDOW_SECONDS = 7 * 24 * 60 * 60


def tracked_statuses(enum_cls: Any, buckets: list[str]) -> list[Any]:
    """The statuses behind the library buckets the user switched on: watching,
    plan to watch and on hold. Used for who gets alerts and which airing shows
    reach the calendar. Completed and Dropped titles are done with and never
    appear in either."""
    by_bucket = {
        "watching": [enum_cls.IN_PROGRESS, enum_cls.REWATCH],
        "plan": [enum_cls.WISHLIST, enum_cls.WATCHLIST],
        "hold": [enum_cls.BACKLOG],
    }
    return [status for bucket in buckets for status in by_bucket.get(bucket, [])]


def _noon_utc(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp())


async def _insert(db: AsyncSession, user_id: UUID, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    return len(await emit_legacy_rows(db, user_id, rows))


def _episode_row(
    media_type: str,
    show: Anime | TVShow,
    season_number: int | None,
    episode_number: int,
    air_at: int,
    *,
    prefs: dict[str, Any],
) -> dict[str, Any] | None:
    started = episode_number == 1
    if started and not prefs["notify_season_started"]:
        return None
    if not started and not prefs["notify_episode_aired"]:
        return None
    later_season = media_type == "tv" and season_number is not None and season_number > 1
    if started:
        kind = "season_started"
        body = (
            f"Season {season_number} has started airing" if later_season else "Has started airing"
        )
    else:
        kind = "episode_aired"
        body = (
            f"Season {season_number} episode {episode_number} aired"
            if later_season
            else f"Episode {episode_number} aired"
        )
    return {
        "kind": kind,
        "media_type": media_type,
        "media_id": show.id,
        "title": show.title,
        "body": body,
        "poster_url": show.poster_url,
        "event_at": air_at,
        "dedupe_key": f"ep:{media_type}:{show.id}:{season_number or 0}:{episode_number}",
    }


@dataclass(frozen=True)
class _EpisodeSource:
    media_type: str
    show_model: type[Anime] | type[TVShow]
    season_model: type[AnimeSeason] | type[TVSeason]
    episode_model: type[AnimeEpisode] | type[TVEpisode]
    status_enum: type[AnimeStatus] | type[TVShowStatus]


_EPISODE_SOURCES = (
    _EpisodeSource("anime", Anime, AnimeSeason, AnimeEpisode, AnimeStatus),
    _EpisodeSource("tv", TVShow, TVSeason, TVEpisode, TVShowStatus),
)


async def _exact_episode_notifications(
    db: AsyncSession,
    user_id: UUID,
    prefs: dict[str, Any],
    window: tuple[int, int],
    source: _EpisodeSource,
) -> tuple[list[dict[str, Any]], set[tuple[Any, int]]]:
    """Episodes with their own exact air time establish the fallback deduplication set."""
    rows: list[dict[str, Any]] = []
    buckets = list(prefs["notify_statuses"])
    ep_stmt = (
        select(
            source.show_model,
            source.season_model.season_number,
            source.episode_model.episode_number,
            source.episode_model.air_at,
        )
        .join(source.season_model, source.season_model.show_id == source.show_model.id)
        .join(source.episode_model, source.episode_model.season_id == source.season_model.id)
        .where(
            source.show_model.user_id == user_id,
            source.show_model.deleted_at.is_(None),
            source.show_model.status.in_(tracked_statuses(source.status_enum, buckets)),
            source.episode_model.air_at.is_not(None),
            source.episode_model.air_at.between(window[0], window[1]),
        )
    )
    seen_numbers: set[tuple[Any, int]] = set()
    for show, season_number, episode_number, air_at in (await db.execute(ep_stmt)).all():
        seen_numbers.add((show.id, episode_number))
        row = _episode_row(
            source.media_type, show, season_number, episode_number, air_at, prefs=prefs
        )
        if row:
            rows.append(row)
    return rows, seen_numbers


async def _next_episode_notifications(
    db: AsyncSession,
    user_id: UUID,
    prefs: dict[str, Any],
    window: tuple[int, int],
    source: _EpisodeSource,
    *,
    seen_numbers: set[tuple[Any, int]],
) -> list[dict[str, Any]]:
    """Use a confirmed next-episode time when no timed episode row already covers it."""
    rows: list[dict[str, Any]] = []
    buckets = list(prefs["notify_statuses"])
    next_stmt = select(source.show_model).where(
        source.show_model.user_id == user_id,
        source.show_model.deleted_at.is_(None),
        source.show_model.status.in_(tracked_statuses(source.status_enum, buckets)),
        source.show_model.next_episode_air_at.is_not(None),
        source.show_model.next_episode_air_at.between(window[0], window[1]),
        source.show_model.next_episode_number.is_not(None),
    )
    # SQLAlchemy infers the shared Base for a union of model classes; this source selects shows.
    for show in cast(Sequence[Anime | TVShow], (await db.execute(next_stmt)).scalars().all()):
        number = show.next_episode_number
        air_at = show.next_episode_air_at
        if number is None or air_at is None or (show.id, number) in seen_numbers:
            continue
        season_number = max((s.season_number for s in show.seasons), default=1)
        row = _episode_row(source.media_type, show, season_number, number, air_at, prefs=prefs)
        if row:
            rows.append(row)
    return rows


async def _movie_notifications(
    db: AsyncSession, user_id: UUID, prefs: dict[str, Any], window: tuple[int, int]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    buckets = list(prefs["notify_statuses"])
    kinds_on = set(prefs["notify_media_types"])
    movie_statuses = tracked_statuses(MovieStatus, [b for b in buckets if b != "watching"])
    if prefs["notify_movie_released"] and "movie" in kinds_on and movie_statuses:
        today = date.today()
        first_day = date.fromtimestamp(window[0])
        movie_stmt = select(Movie).where(
            Movie.user_id == user_id,
            Movie.deleted_at.is_(None),
            Movie.status.in_(movie_statuses),
            Movie.release_date.is_not(None),
            Movie.release_date.between(first_day, today),
        )
        for movie in (await db.execute(movie_stmt)).scalars().all():
            if movie.release_date is None:
                continue
            rows.append(
                {
                    "kind": "movie_released",
                    "media_type": "movie",
                    "media_id": movie.id,
                    "title": movie.title,
                    "body": "Released",
                    "poster_url": movie.poster_url,
                    "event_at": _noon_utc(movie.release_date),
                    "dedupe_key": f"movie:{movie.id}",
                }
            )

    return rows


async def generate_for_user(db: AsyncSession, user_id: UUID) -> int:
    """Creates any notifications that are due. Returns how many were new."""
    prefs = await load_preferences(db, user_id)
    now = int(time.time())
    since = now - WINDOW_SECONDS
    rows: list[dict[str, Any]] = []

    # Inbox expiry does not cancel external sends. Keep transport content until
    # queued work finishes; minimal receipts prevent deleted history reappearing.
    retention_days = retention_policy(prefs)["effective_days"]
    if retention_days:
        cutoff = now - retention_days * 86400
        await change_notices(
            db,
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.event_at < cutoff,
                Notification.inbox_visible.is_(True),
            )
            .values(inbox_visible=False),
            "expired",
        )
        await db.execute(
            delete(Notification).where(
                Notification.user_id == user_id,
                Notification.event_at < cutoff,
                ~Notification.id.in_(
                    select(NotificationDelivery.notification_id).where(
                        NotificationDelivery.status.in_(("pending", "processing", "retry_wait"))
                    )
                ),
            )
        )

    window = (since, now)
    for source in _EPISODE_SOURCES:
        if source.media_type not in prefs["notify_media_types"] or not prefs["notify_statuses"]:
            continue
        exact_rows, seen_numbers = await _exact_episode_notifications(
            db, user_id, prefs, window, source
        )
        rows.extend(exact_rows)
        rows.extend(
            await _next_episode_notifications(
                db, user_id, prefs, window, source, seen_numbers=seen_numbers
            )
        )
    rows.extend(await _movie_notifications(db, user_id, prefs, window))
    created = await _insert(db, user_id, rows)
    created += await generate_game_releases(db, user_id, now=now)
    await db.commit()
    return created


COMPLETED_ANIME = (AnimeStatus.WATCHED, AnimeStatus.FAVORITE)
_SEASON_FORMATS = {"tv", "tv short", "ona"}
_EXTRA_FORMATS = {"movie": "movie", "ova": "OVA", "special": "special"}


def anilist_entry_id(value: Any) -> int | None:
    """AniList identities are positive integers, in graphs or as stored library strings."""
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def franchise_entry_ids(cache: dict[str, Any] | None) -> set[int]:
    """Every AniList entry a stored franchise graph has listed, including earlier fetches."""
    if not isinstance(cache, dict):
        return set()
    listed = [
        node.get("id")
        for group in ("chain", "branches")
        for node in cache.get(group) or []
        if isinstance(node, dict)
    ]
    known = cache.get("known_ids")
    ids = {
        anilist_entry_id(value) for value in [*listed, *(known if isinstance(known, list) else [])]
    }
    return {value for value in ids if value is not None}


def _enabled_kind(prefs: dict[str, Any], kinds: tuple[str, ...]) -> str | None:
    """The first host type this account accepts, using the dispatcher's own preference gate."""
    return next(
        (
            kind
            for kind in kinds
            if preference_enabled(Notification(kind=kind, event_type=EVENT_TYPES[kind]), prefs)
        ),
        None,
    )


async def _franchise_siblings(db: AsyncSession, show: Anime, entry_ids: list[int]) -> list[Any]:
    """The owner's other active titles among a graph's listed entries."""
    return list(
        (
            await db.execute(
                select(
                    Anime.id,
                    Anime.title,
                    Anime.poster_url,
                    Anime.status,
                    Anime.anilist_id,
                    Anime.relations_cache,
                ).where(
                    Anime.user_id == show.user_id,
                    Anime.deleted_at.is_(None),
                    Anime.id != show.id,
                    Anime.anilist_id.in_([str(entry_id) for entry_id in entry_ids]),
                )
            )
        ).all()
    )


def _listing_notice(
    prefs: dict[str, Any], node: dict[str, Any], *, in_sequence: bool, scheduled: bool
) -> tuple[str | None, str, bool]:
    """Classify one newly listed entry as (kind, body, deferred); kind None sends nothing."""
    listing = str(node.get("format") or "").lower()
    title = str(node.get("title") or "Untitled")
    if listing in _SEASON_FORMATS and in_sequence:
        kinds = ("franchise_season",) if scheduled else ("franchise_season", "sequel_announced")
        kind = _enabled_kind(prefs, kinds)
        deferred = kind is None and scheduled and bool(_enabled_kind(prefs, ("sequel_announced",)))
        return kind, f"A new season is listed: {title}", deferred
    if listing in _EXTRA_FORMATS:
        kind = _enabled_kind(prefs, ("franchise_extra",))
        return kind, f"A new {_EXTRA_FORMATS[listing]} is listed: {title}", False
    return None, "", False


@dataclass(frozen=True)
class _FranchiseBaseline:
    known: set[int | None]
    # The completed title a notice links to; an ORM title or a selected sibling row.
    anchor: Any


async def _franchise_baseline(
    db: AsyncSession,
    show: Anime,
    old_cache: dict[str, Any] | None,
    listed: dict[int, dict[str, Any]],
    position: dict[int, int],
) -> _FranchiseBaseline | None:
    """Unite the owner's stored graphs for this franchise; None when nothing may notify."""
    siblings = await _franchise_siblings(db, show, list(listed))
    if old_cache is None and all(row.relations_cache is None for row in siblings):
        return None
    # The ORM title and its siblings' selected rows share these attribute names.
    titles: list[Any] = [show, *siblings]
    completed = [row for row in titles if row.status in COMPLETED_ANIME]
    if not completed:
        return None
    known = franchise_entry_ids(old_cache).union(
        *(franchise_entry_ids(row.relations_cache) for row in siblings),
        {anilist_entry_id(row.anilist_id) for row in titles},
    )
    # Attach notices to the completed title closest to the new entry in the sequence.
    anchor = max(completed, key=lambda row: position.get(anilist_entry_id(row.anilist_id) or 0, -1))
    return _FranchiseBaseline(known, anchor)


def _franchise_rows(
    prefs: dict[str, Any],
    listed: dict[int, dict[str, Any]],
    position: dict[int, int],
    baseline: _FranchiseBaseline,
    scheduled: bool,
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    deferred = False
    for entry_id, node in listed.items():
        if entry_id in baseline.known or node.get("is_current"):
            continue
        kind, body, later = _listing_notice(
            prefs, node, in_sequence=entry_id in position, scheduled=scheduled
        )
        deferred = deferred or later
        if kind is not None:
            rows.append(
                {
                    "kind": kind,
                    "media_type": "anime",
                    "media_id": baseline.anchor.id,
                    "title": baseline.anchor.title,
                    "body": body,
                    "poster_url": baseline.anchor.poster_url,
                    "event_at": int(time.time()),
                    "dedupe_key": f"anime-franchise:{entry_id}",
                }
            )
    return rows, deferred


async def record_franchise_changes(
    db: AsyncSession,
    show: Anime,
    old_cache: dict[str, Any] | None,
    graph: dict[str, Any],
    *,
    scheduled: bool = False,
) -> tuple[int, bool]:
    """Announce entries newly listed in an anime franchise the owner completed.

    The baseline unites every graph stored on the owner's titles in this franchise, and
    the dedupe key names the new AniList entry, so one listing is announced once per
    account whichever title or check finds it. A first fetch with no stored graph
    anywhere in the franchise only records a baseline. Titles already in the library are
    skipped. AniList does not date announcements, so the time is when it was learned.

    New seasons use the opt-in franchise type, otherwise the existing sequel
    announcement; movies, OVAs and specials have their own opt-in type. A scheduled check
    never sends a sequel announcement on an account's behalf: it returns deferred=True so
    the caller keeps the old graph and opening the title can still announce it. Returns
    (created, deferred); the caller owns the transaction."""
    prefs = await load_preferences(db, show.user_id)
    if show.deleted_at is not None or "anime" not in prefs["notify_media_types"]:
        return 0, False
    chain = [node for node in graph.get("chain") or [] if anilist_entry_id(node.get("id"))]
    branches = [node for node in graph.get("branches") or [] if anilist_entry_id(node.get("id"))]
    listed = {node["id"]: node for node in [*branches, *chain]}
    position = {node["id"]: index for index, node in enumerate(chain)}
    baseline = await _franchise_baseline(db, show, old_cache, listed, position)
    if baseline is None:
        return 0, False
    rows, deferred = _franchise_rows(prefs, listed, position, baseline, scheduled)
    return await _insert(db, show.user_id, rows), deferred


async def record_sequel_announcements(
    db: AsyncSession,
    user_id: UUID,
    show: Anime,
    old_chain: list[dict[str, Any]] | None,
    new_chain: list[dict[str, Any]],
) -> None:
    """A finished anime whose franchise chain grew a new season since the
    last look. Only fires for a chain that was fetched before (there is a
    baseline to compare against) so the first fetch never notifies, and
    only for shows the user completed. The time is when we learned of it,
    since AniList does not say when a sequel was announced."""
    prefs = await load_preferences(db, user_id)
    if not prefs["notify_sequel_announced"] or "anime" not in prefs["notify_media_types"]:
        return
    if old_chain is None:
        return
    if show.status not in (AnimeStatus.WATCHED, AnimeStatus.FAVORITE):
        return
    known = {n["id"] for n in old_chain}
    rows: list[dict[str, Any]] = []
    for node in new_chain:
        if node["id"] in known or node.get("is_current"):
            continue
        fmt = (node.get("format") or "").lower()
        if fmt not in ("tv", "ona", "tv short"):
            continue
        rows.append(
            {
                "kind": "sequel_announced",
                "media_type": "anime",
                "media_id": show.id,
                "title": show.title,
                "body": f"A new season is listed: {node['title']}",
                "poster_url": show.poster_url,
                "event_at": int(time.time()),
                "dedupe_key": f"seq:{show.id}:{node['id']}",
            }
        )
    if await _insert(db, user_id, rows):
        await db.commit()
