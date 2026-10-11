"""Shared achievement reads for immediate and batched Steam imports."""

import asyncio

from src.features.metadata.games import steam, steam_session
from src.helpers.steam_achievement_rows import needs_community_descriptions

SteamAchievementData = tuple[dict[str, dict], list[dict] | None, dict[str, str] | None]


async def fetch_steam_achievements(
    steam_id: str, api_key: str | steam.SteamSession, app_id: int
) -> SteamAchievementData:
    """None player rows mean unavailable progress; an empty schema is valid."""
    if isinstance(api_key, steam.SteamSession):
        return await _fetch_owner_achievements(steam_id, api_key, app_id)
    try:
        schema = await asyncio.to_thread(steam.get_schema_for_game, api_key, app_id)
    except steam.SteamLibraryError:
        return {}, None, None
    if not schema:
        return {}, [], None
    try:
        unlocked = await asyncio.to_thread(steam.get_player_achievements, steam_id, api_key, app_id)
    except steam.SteamLibraryError:
        return schema, None, None
    if not unlocked:
        return schema, None, None
    descriptions = None
    if needs_community_descriptions(schema):
        descriptions = await asyncio.to_thread(steam.get_community_descriptions, steam_id, app_id)
    return schema, unlocked, descriptions


async def _fetch_owner_achievements(
    steam_id: str, session: steam.SteamSession, app_id: int
) -> SteamAchievementData:
    """Owner reads use Steam's versioned internal-key schema, not public stats."""
    if steam_id != session.steam_id:
        return {}, None, None
    try:
        schema, unlocked = await asyncio.to_thread(
            steam_session.get_achievement_data, steam.SESSION, session, app_id
        )
    except steam.SteamLibraryError:
        return {}, None, None
    return schema, unlocked, None
