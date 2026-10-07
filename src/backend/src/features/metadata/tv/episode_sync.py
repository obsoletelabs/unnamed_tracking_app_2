"""Canonical TV episode/airing lookups shared by routes and background refresh."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.features.metadata.service import collect_owned_record, library_candidate
from src.plugin_api.metadata_contracts import MediaType


async def fetch_season_episodes(
    external_id: str | None, season_number: int, *, user_id: UUID, title: str
) -> tuple[list[dict[str, Any]], list[str]]:
    if not external_id:
        return [], []
    record, errors = await collect_owned_record(
        user_id,
        library_candidate(title, MediaType.TV_SHOW, {"tvmaze": external_id}),
        resource="episodes",
        season_number=season_number,
    )
    return [
        entry
        for entry in record.get("metadata", {}).get("episodes", [])
        if entry.get("season_number") == season_number
    ], errors


async def fetch_is_airing(
    external_id: str | None, *, user_id: UUID, title: str
) -> tuple[bool | None, list[str]]:
    if not external_id:
        return None, []
    record, errors = await collect_owned_record(
        user_id,
        library_candidate(title, MediaType.TV_SHOW, {"tvmaze": external_id}),
        resource="airing",
    )
    return record.get("metadata", {}).get("airing", {}).get("is_airing"), errors


async def fetch_next_episode(
    external_id: str | None, *, user_id: UUID, title: str
) -> tuple[int | None, int | None, list[str]]:
    if not external_id:
        return None, None, []
    record, errors = await collect_owned_record(
        user_id,
        library_candidate(title, MediaType.TV_SHOW, {"tvmaze": external_id}),
        resource="airing",
    )
    airing = record.get("metadata", {}).get("airing", {})
    return airing.get("next_episode_at"), airing.get("next_episode_number"), errors
