"""Notices when a TV show you follow gets a new season.

TVmaze lists a show's seasons, announced ones included. When it lists a
season number higher than any we have, that season is added to the show
(so it appears on the title page and the library moves onto it) and, for a
show you are watching or have finished, a "new season listed" notification
is created with the season's premiere date when TVmaze has one.

There is no scheduler: opening a show's page asks for a check when the
last one is more than 3 days old, in the background, so a new season shows
up the next time you look. The very first check of a show adds seasons
silently, so a show added long ago does not produce a burst of old news."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.tv_show import TVSeason, TVShow, TVShowStatus
from src.database.session import SessionLocal
from src.features.metadata.service import collect_record, library_candidate
from src.features.notification_controller import emit_legacy_rows
from src.plugin_api.metadata_contracts import MediaType

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 3 * 24 * 60 * 60
_inflight: set[UUID] = set()

_NOTIFY_STATUSES = (
    TVShowStatus.IN_PROGRESS,
    TVShowStatus.REWATCH,
    TVShowStatus.WATCHED,
    TVShowStatus.FAVORITE,
    TVShowStatus.BACKLOG,
)


def new_seasons(known_numbers: set[int], listed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Listed seasons numbered above every season we have. Only higher
    numbers count, so a difference in how a provider numbers old seasons
    (specials, splits) can never rewrite the structure of a show."""
    highest = max(known_numbers, default=0)
    return [s for s in listed if s["season_number"] > highest]


async def check_new_seasons(db: AsyncSession, show: TVShow) -> int:
    """Adds any new seasons and notifies. Returns how many were added."""
    first_check = show.seasons_checked_at is None
    ids = dict(show.provider_ids or {})
    if show.external_id:
        ids.setdefault("tvmaze", show.external_id)
    record, errors = await collect_record(
        db,
        show.user_id,
        library_candidate(
            show.title,
            MediaType.TV_SHOW,
            ids,
            show.first_air_date.year if show.first_air_date else None,
        ),
    )
    seasons = record.get("metadata", {}).get("seasons")
    if seasons is None or errors and not seasons:
        return 0
    listed = [{**entry, "name": entry.get("title")} for entry in seasons]

    # read from the database, not the (possibly stale) loaded collection
    known = set(
        (await db.execute(select(TVSeason.season_number).where(TVSeason.show_id == show.id)))
        .scalars()
        .all()
    )
    fresh = new_seasons(known, listed)
    for entry in fresh:
        db.add(
            TVSeason(
                show_id=show.id,
                season_number=entry["season_number"],
                name=entry["name"],
                episode_count=entry["episode_count"],
                air_date=date.fromisoformat(entry["air_date"]) if entry["air_date"] else None,
            )
        )
    show.seasons_checked_at = int(time.time())

    if fresh and not first_check and show.status in _NOTIFY_STATUSES:
        prefs = await load_preferences(db, show.user_id)
        if prefs["notify_sequel_announced"] and "tv" in prefs["notify_media_types"]:
            now = int(time.time())
            rows = []
            for entry in fresh:
                when = f", premiering {entry['air_date']}" if entry["air_date"] else ""
                rows.append(
                    {
                        "user_id": show.user_id,
                        "kind": "sequel_announced",
                        "media_type": "tv",
                        "media_id": show.id,
                        "title": show.title,
                        "body": f"Season {entry['season_number']} is listed{when}",
                        "poster_url": show.poster_url,
                        "event_at": now,
                        "dedupe_key": f"season:tv:{show.id}:{entry['season_number']}",
                        "created_at": now,
                    }
                )
            await emit_legacy_rows(db, show.user_id, rows)
    await db.commit()
    return len(fresh)


def is_due(show: TVShow) -> bool:
    if not show.external_id:
        return False
    checked = show.seasons_checked_at
    return checked is None or time.time() - checked > CHECK_INTERVAL_SECONDS


async def check_in_background(show_id: UUID) -> None:
    """Runs the check without making the page wait for TVmaze."""
    if show_id in _inflight:
        return
    _inflight.add(show_id)
    try:
        async with SessionLocal() as db:
            show = await db.scalar(select(TVShow).where(TVShow.id == show_id))
            if show is not None:
                await check_new_seasons(db, show)
    # A failed background lookup must release its in-flight slot and leave page requests usable.
    # pylint: disable-next=broad-exception-caught
    except Exception:
        logger.exception("Background season check failed for %s", show_id)
    finally:
        _inflight.discard(show_id)
