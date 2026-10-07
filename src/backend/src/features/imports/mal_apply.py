"""Matching a MyAnimeList list against the library, and applying it.

For a title that is already on the site the user chooses, one title at a
time, between keeping it exactly as it is and taking MAL's tracking data.
Taking MAL's data changes only what MAL actually states: a MAL score of 0
(unrated), an empty comment or a missing date never wipes something already
on the site. Tags are merged, never replaced. Progress is set only when the
title has a single season, since a MAL entry is one series and has no way to
say which of several seasons it means.

Posters, genres, studios and the like are then filled from AniList in
batches by MAL id, and only where the site's own field is still blank, so
nothing already there is overwritten by metadata."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.titles import apply_alt_titles
from src.database.models.anime import Anime, AnimeSeason
from src.features.episode_progress import apply_counter
from src.features.imports.mal import MalEntry
from src.features.metadata.service import library_candidate, media_result, resolve_owned_record
from src.plugin_api.metadata_contracts import MediaType


@dataclass
class Match:
    entry: MalEntry
    existing: Anime | None


def _label(status: Any) -> str:
    return str(getattr(status, "value", status)).replace("_", " ").title()


async def match_entries(db: AsyncSession, user_id: Any, entries: list[MalEntry]) -> list[Match]:
    shows = (
        (
            await db.execute(
                select(Anime)
                .where(Anime.user_id == user_id, Anime.deleted_at.is_(None))
                .options(selectinload(Anime.seasons).selectinload(AnimeSeason.episodes))
            )
        )
        .scalars()
        .all()
    )
    by_id = {s.external_id: s for s in shows if s.external_id}
    by_title = {s.title.lower(): s for s in shows}
    return [Match(e, by_id.get(e.mal_id) or by_title.get(e.title.lower())) for e in entries]


def differences(show: Anime, entry: MalEntry) -> list[dict[str, Any]]:
    """What taking MAL's data would change, as site value vs MAL value."""
    out: list[dict[str, Any]] = []

    def add(field: str, site: Any, mal: Any) -> None:
        if mal is not None and site != mal:
            out.append(
                {"field": field, "site": None if site is None else str(site), "mal": str(mal)}
            )

    add("Status", _label(show.status), _label(entry.status) if entry.status else None)
    if len(show.seasons) == 1:
        add("Episodes watched", show.seasons[0].episodes_watched, entry.watched)
    add(
        "Score",
        None if show.rating_overall is None else float(show.rating_overall),
        None if entry.score is None else float(entry.score),
    )
    add("Rewatches", show.rewatches, entry.rewatches if entry.rewatches else None)
    add("Started", show.start_date, entry.started)
    add("Finished", show.end_date, entry.finished)
    if entry.comment and entry.comment != show.note:
        out.append({"field": "Note", "site": show.note, "mal": entry.comment})
    return out


def apply_tracking(show: Anime, entry: MalEntry) -> None:
    show.status = entry.status
    if entry.score is not None:
        show.rating_overall = entry.score
    if entry.rewatches:
        show.rewatches = entry.rewatches
    if entry.started:
        show.start_date = entry.started
    if entry.finished:
        show.end_date = entry.finished
    if entry.comment:
        show.note = entry.comment
    if entry.tags:
        show.tags = list(dict.fromkeys([*(show.tags or []), *entry.tags]))
    if not show.external_id:
        show.external_id = entry.mal_id
    if not show.format and entry.format:
        show.format = entry.format
    if len(show.seasons) == 1:
        season = show.seasons[0]
        if entry.episodes and season.episode_count is None:
            season.episode_count = entry.episodes
        season.episodes_watched = entry.watched
        apply_counter(season, entry.watched)
        season.status = entry.status


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


# (show attribute, AniList entry key), filled only where the site is blank
_BLANK_ONLY = (
    ("format", "format"),
    ("poster_url", "poster_url"),
    ("backdrop_url", "backdrop_url"),
    ("description", "overview"),
    ("studios", "studios"),
    ("countries", "countries"),
    ("genres", "genres"),
    ("episode_runtime_minutes", "episode_runtime_minutes"),
    ("anilist_score", "score"),
)


def fill_blanks(show: Anime, meta: dict[str, Any]) -> bool:
    changed = False
    for attr, key in _BLANK_ONLY:
        if attr not in (show.locked_fields or []) and not getattr(show, attr) and meta.get(key):
            setattr(show, attr, meta[key])
            changed = True
    if apply_alt_titles(show, meta):
        changed = True
    if not show.anilist_id and meta.get("id"):
        show.anilist_id = str(meta["id"])
        changed = True
    if not show.first_air_date and (day := _date(meta.get("release_date"))):
        show.first_air_date = day
        changed = True
    if show.seasons and show.seasons[0].episode_count is None and meta.get("episode_count"):
        show.seasons[0].episode_count = meta["episode_count"]
        changed = True
    return changed


async def fill_details(shows: list[Anime]) -> dict[str, int]:
    """Fill blank import fields through the same owner-scoped provider contract."""
    slots = asyncio.Semaphore(6)

    async def one(show: Anime) -> tuple[bool, bool, bool]:
        async with slots:
            ids = dict(show.provider_ids or {})
            if show.external_id:
                ids.setdefault("mal", show.external_id)
            if show.anilist_id:
                ids.setdefault("anilist", show.anilist_id)
            record, errors = await resolve_owned_record(show.user_id, library_candidate(
                show.title, MediaType.ANIME, ids,
                show.first_air_date.year if show.first_air_date else None,
            ), include_media=True)
            if not record.get("metadata") and not record.get("assets"):
                return False, not bool(errors), bool(errors)
            meta = media_result(record, MediaType.ANIME)
            meta.update({"overview": meta.get("description"), "release_date": meta.get("first_air_date"),
                         "score": meta.get("anilist_score"), "id": meta.get("anilist_id")})
            show.provider_ids = {**ids, **record.get("provider_ids", {})}
            return fill_blanks(show, meta), False, False

    results = await asyncio.gather(*(one(show) for show in shows))
    return dict(zip(("filled", "not_found", "lookup_failed"),
                    (sum(row[index] for row in results) for index in range(3)), strict=True))
