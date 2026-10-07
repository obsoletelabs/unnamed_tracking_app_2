"""Filling in an anime's English, romaji and Japanese spellings from AniList,
through registered metadata providers. Shared by the "look up titles" button and
the media refresh, so a title added or imported without them gets them on the
next refresh. Only blank title fields are set, nothing else changes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.titles import apply_alt_titles
from src.database.models.anime import Anime
from src.features.metadata.anime.episode_sync import anime_candidate
from src.features.metadata.service import collect_record


async def fill_missing_titles(db: AsyncSession, user_id: UUID | None = None) -> dict[str, Any]:
    """Looks up every anime (one user's, or everyone's) that has none of the
    three spellings. Titles with neither an AniList nor a MyAnimeList id cannot
    be looked up and are counted."""
    stmt = select(Anime).where(
        Anime.deleted_at.is_(None),
        Anime.title_english.is_(None),
        Anime.title_romaji.is_(None),
        Anime.title_native.is_(None),
    )
    if user_id is not None:
        stmt = stmt.where(Anime.user_id == user_id)
    shows = list((await db.execute(stmt)).scalars().all())
    filled = lookup_failed = 0
    for show in shows:
        if not show.anilist_id and not show.external_id:
            continue
        record, errors = await collect_record(
            db, show.user_id, anime_candidate(show.title, show.external_id, show.anilist_id)
        )
        titles = record.get("metadata", {}).get("titles", {})
        if errors and not titles:
            lookup_failed += 1
        if apply_alt_titles(show, {"title_" + key: value for key, value in titles.items()}):
            filled += 1
    await db.commit()
    return {
        "filled": filled,
        "without_id": sum(1 for s in shows if not s.anilist_id and not s.external_id),
        "lookup_failed": lookup_failed,
        "checked": len(shows),
    }
