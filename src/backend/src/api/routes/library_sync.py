"""API routes for pulling a user's owned-games library + achievements from
Steam, PlayStation, and RetroAchievements straight into their library — a
different mechanism from `games.py`'s `/metadata/search`, which enriches one
already-added game by title. This creates/updates real `Game` rows and
`Achievement` rows in bulk from an account-wide library call.

Xbox is deliberately not here: a real library pull needs a full OAuth
consent redirect (Microsoft identity platform + Xbox Live XASU/XSTS token
exchange) that requires the user's own registered Azure AD app and a
redirect URI this server hosts — Settings only stores the client
id/secret today (see MetadataSourcesSection's Xbox card), it can't complete
that flow yet.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import time
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.settings import (
    get_or_create_scan_settings,
)
from src.api.routes.utils.games import _scan_settings_to_preferences
from src.core.app_integrations import get_max_upload_size_mb
from src.core.auth import get_current_user
from src.core.crypto import decrypt_secret
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal, get_db
from src.features import achievement_sync as _achievement_sync
from src.features import game_folders
from src.features.achievement_catalogue import fetch_catalogue
from src.features.achievement_refresh import (
    AchievementRefreshSources,
)
from src.features.achievement_refresh import (
    fetch_psn_rows as _fetch_psn_rows_impl,
)
from src.features.achievement_refresh import (
    fetch_retro_rows as _fetch_retro_rows,
)
from src.features.achievement_refresh import (
    fetch_steam_rows as _fetch_steam_rows,
)
from src.features.achievement_refresh import (
    refresh_game_achievements as _refresh_achievement_progress,
)
from src.features.achievement_sync import (
    psn_trophy_rows as _psn_trophy_rows,
)
from src.features.achievement_sync import (
    replace_achievements as _replace_achievements,
)
from src.features.achievement_sync import (
    retro_achievement_rows as _retro_achievement_rows,
)
from src.features.connection_jobs import sync_scope
from src.features.game_identity import names
from src.features.game_ingestion import legacy as ingestion
from src.features.game_ingestion.contract import SourceObservationV1
from src.features.imports import steam_connection
from src.features.imports.game_progression import apply_provider_progress
from src.features.imports.library_games import LibraryIndex
from src.features.imports.library_games import get_or_create_game as _get_or_create_game
from src.features.imports.steam_achievements import SteamAchievementData, fetch_steam_achievements
from src.features.metadata.games import steam
from src.features.metadata.games.psn import PSNClient, PSNError
from src.features.metadata.games.retroachievements import (
    RetroAchievementsClient,
    RetroAchievementsError,
)
from src.features.metadata.locked_fields import apply_metadata_updates
from src.features.metadata.service import game_candidate, game_result, resolve_library_record
from src.helpers import save_game_asset
from src.helpers.remote_images import MAX_BYTES, RemoteImageError, download_image
from src.helpers.save_game_asset import ASSET_FILENAMES, AssetKind, save_locked_game_asset
from src.helpers.steam_achievement_rows import (  # noqa: F401
    needs_community_descriptions as _needs_community_descriptions,
)
from src.helpers.steam_achievement_rows import (
    steam_achievement_rows as _steam_achievement_rows,
)

router = APIRouter(
    prefix="/api/library-sync", tags=["library-sync"], dependencies=[Depends(get_current_user)]
)

# Keep this legacy helper available to callers that imported it from this route module.
_unix_from_text = _achievement_sync._unix_from_text  # pylint: disable=protected-access

_SYNC_CONCURRENCY = 5


async def _fetch_psn_rows(user: User, external_id: str, platform: str | None) -> list[dict]:
    """Keep PSN client dependencies injectable through this route module."""
    return await _fetch_psn_rows_impl(
        user,
        external_id,
        platform,
        secret_decryptor=decrypt_secret,
        client_factory=PSNClient,
    )


# Steam's owned-games list includes non-game companion apps alongside real
# games — public playtests, test/staging servers, and Valve's own
# placeholder for a delisted/renamed app. None of these are things a user
# is tracking as a "game" to beat/master, so they're skipped entirely
# during sync rather than imported with (inevitably blank) metadata.
_JUNK_TITLE_PATTERN = re.compile(
    r"(playtest|public test\b|test server|testing branch|staging branch|dedicated server)"
    r"|^game migrated to another steam page$",
    re.IGNORECASE,
)


def _is_junk_title(title: str) -> bool:
    return bool(_JUNK_TITLE_PATTERN.search(title))


async def _enrich_steam_game_by_appid(
    game: Game,
    app_id: int,
    user: User,
    use_user_tags: bool = True,
    *,
    artwork_only: bool = False,
) -> "EnrichOutcome":
    """Owned-game IDs anchor enrichment without another title match."""
    game.provider_ids = {**(game.provider_ids or {}), "steam": str(app_id)}
    return await _enrich_new_game(
        game, user, {"steam_user_tags": use_user_tags}, artwork_only=artwork_only
    )


def _add_source_tag_and_collection(game: Game, source: str) -> None:
    """A newly-synced game is tagged with its source (Steam, PlayStation,
    RetroAchievements) and dropped into a same-named collection by default
    — so a library built from several accounts stays sortable by where
    each game actually came from without the user manually tagging
    hundreds of imported titles. Only runs for newly-created games (see
    each sync route's `if created:` guard), same rule as
    `_add_to_series_collection`: a user untagging/removing one later isn't
    silently re-added on the next re-sync."""
    if source not in game.tags:
        game.tags = [*game.tags, source]
    if source not in game.collections:
        game.collections = [*game.collections, source]


def _add_to_series_collection(game: Game, series: str) -> None:
    """Games in the same series get auto-grouped into a same-named
    collection — this is what the future card/set system will build on, so
    it needs to already be populated by the time that ships, not
    retrofitted later. Only ever adds; a user removing a game from this
    collection later isn't re-added on the next sync since this only runs
    for newly-created games (see `_enrich_new_game`/
    `_enrich_steam_game_by_appid`'s callers)."""
    if series not in game.collections:
        game.collections = [*game.collections, series]


async def _download_asset(
    url: str, game: Game, asset_kind: AssetKind, *, max_bytes: int = MAX_BYTES
) -> bool:
    """Best-effort — mirrors games.py's download_game_asset route but never
    raises, since one bad art URL must not fail an entire library sync.
    Returns whether it actually saved something, so callers can fall back
    to a different URL."""
    try:
        content = await asyncio.to_thread(download_image, url, max_bytes=max_bytes)
        await save_locked_game_asset(content, game, asset_kind)
        return True
    except (RemoteImageError, OSError, ValueError):
        return False


@dataclasses.dataclass
class EnrichOutcome:
    """What one enrichment pass achieved, so an import can report it honestly (#256)."""

    metadata_found: bool = False
    # pictures the game still lacks after the pass: the cover when none was found, and any picture
    # the provider offered but could not be downloaded (also listed in `artwork_download_failed`)
    artwork_missing: list[str] = dataclasses.field(default_factory=list)
    artwork_download_failed: list[str] = dataclasses.field(default_factory=list)


_ASSET_KINDS: tuple[tuple[AssetKind, str], ...] = (
    ("key_art", "key_art_url"),
    ("banner", "banner_url"),
    ("logo", "logo_url"),
    ("icon", "icon_url"),
)


def _asset_present(game: Game, asset_kind: AssetKind) -> bool:
    """Whether the game already has this picture on disk (artwork lives in the game folder)."""
    if not game.folder_location:
        return False
    try:
        folder = game_folders.owned_folder(
            save_game_asset.DATA_ROOT, game.user_id, game.folder_location
        )
    except game_folders.FolderStorageError:
        return False
    return (folder / ASSET_FILENAMES[asset_kind]).is_file()


async def _enrich_new_game(
    game: Game, user: User, preferences: dict, *, artwork_only: bool = False
) -> EnrichOutcome:
    """Fill newly imported rows through the same scoped metadata and media operations.

    `artwork_only` is the retry mode: it never rewrites details a person may since have edited
    and only fetches pictures the game does not already have."""
    outcome = EnrichOutcome()
    candidate = game_candidate(game)
    async with SessionLocal() as metadata_db:
        record, _ = await resolve_library_record(
            metadata_db,
            user.id,
            candidate,
            include_media=True,
            preferences=preferences,
        )
        max_image_bytes = MAX_BYTES
        if record and record.get("assets"):
            max_image_bytes = (await get_max_upload_size_mb(metadata_db)) * 1024 * 1024
    if not record:
        if not _asset_present(game, "key_art"):
            outcome.artwork_missing = ["key_art"]
        return outcome
    outcome.metadata_found = True
    match = game_result(record)
    if artwork_only:
        return await _fetch_missing_artwork(game, match, max_image_bytes, outcome)
    game.provider_ids = {**(game.provider_ids or {}), **record.get("provider_ids", {})}
    if match.get("title") and names.is_weak(
        names.classify_title(game.title, source=game.source, external_id=game.external_id)
    ):
        # Trusted metadata upgrades a recognised placeholder; it never renames a real title.
        names.apply_candidate_title(game, match["title"], trusted_metadata=True)
    for field in (
        "description",
        "developer",
        "publisher",
        "series",
        "age_rating",
        "tags",
        "features",
        "time_to_beat_hours",
        "time_to_beat_extras_hours",
        "time_to_beat_completionist_hours",
        "time_to_beat_all_hours",
    ):
        if field.startswith("time_to_beat") and not preferences.get("save_time_to_beat", True):
            continue
        value = match.get(field)
        if value:
            apply_metadata_updates(game, {field: value})
    if match.get("series"):
        _add_to_series_collection(game, match["series"])
    release_date = match.get("release_date")
    if release_date:
        try:
            apply_metadata_updates(game, {"release_date": date.fromisoformat(release_date)})
        except ValueError:
            pass
    return await _fetch_missing_artwork(game, match, max_image_bytes, outcome, overwrite=True)


async def _fetch_missing_artwork(
    game: Game,
    match: dict,
    max_image_bytes: int,
    outcome: EnrichOutcome,
    *,
    overwrite: bool = False,
) -> EnrichOutcome:
    for asset_kind, field_name in _ASSET_KINDS:
        if not overwrite and _asset_present(game, asset_kind):
            continue
        urls = match.get(field_name + "s", [])
        saved = False
        for url in urls:
            if await _download_asset(url, game, asset_kind, max_bytes=max_image_bytes):
                saved = True
                break
        if saved or _asset_present(game, asset_kind):
            continue
        if urls:  # offered, but every download failed
            outcome.artwork_missing.append(asset_kind)
            outcome.artwork_download_failed.append(asset_kind)
        elif asset_kind == "key_art":  # the cover is the picture a game card needs
            outcome.artwork_missing.append(asset_kind)
    return outcome


async def _flag_stale_games(
    db: AsyncSession, user_id: UUID, source: str, touched_ids: set[UUID]
) -> int:
    """After a full sync pass, any active game from this source that wasn't
    touched this time has disappeared from the account's owned-games pull
    (uninstalled, refunded, family-shared game removed, etc.) — flag it
    rather than removing or overwriting anything, since the API alone can't
    tell "gone for good" from "temporarily delisted." A game that reappears
    on a later sync has its flag cleared in _get_or_create_game."""
    result = await db.execute(
        select(Game).where(
            Game.user_id == user_id,
            Game.source == source,
            Game.deleted_at.is_(None),
            # a wishlisted game is not in the owned list by definition
            Game.status != GameStatus.WISHLIST,
        )
    )
    now = int(time.time())
    newly_flagged = 0
    for game in result.scalars().all():
        if game.id in touched_ids:
            continue
        if game.stale_since is None:
            game.stale_since = now
            newly_flagged += 1
    return newly_flagged


@router.post("/games/{game_id}/achievements")
async def refresh_game_achievements(
    game_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Refresh provider progress and metadata definitions for one game."""
    sources = AchievementRefreshSources(
        fetch_steam_rows=_fetch_steam_rows,
        fetch_retro_rows=_fetch_retro_rows,
        fetch_psn_rows=_fetch_psn_rows,
        fetch_catalogue=fetch_catalogue,
    )
    return await _refresh_achievement_progress(game_id, db, current_user, sources)


# Keep the established workflow and public parameters together.
# pylint: disable=duplicate-code,too-many-locals,too-many-statements
@router.post("/steam")
async def sync_steam_library(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    achievements: Literal["now", "later"] = "now",
) -> dict:
    """Use the same inventory guard for foreground and scheduled imports."""
    async with sync_scope(current_user.id, "steam"):
        return await _sync_steam_library(db, current_user, achievements)


async def _sync_steam_library(
    db: AsyncSession, current_user: User, achievements: Literal["now", "later"]
) -> dict:
    try:
        steam_id, api_key = await steam_connection.credentials(db, current_user)
        owned_games = await asyncio.to_thread(steam.get_owned_games, steam_id, api_key)
    except steam_connection.SteamConnectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    except steam.SteamLibraryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # drop playtests/test servers/Valve's delisted-app placeholder before
    # any further work — they're companion apps, not games to track
    owned_games = [
        e for e in owned_games if e.get("appid") and e.get("name") and not _is_junk_title(e["name"])
    ]

    semaphore = asyncio.Semaphore(_SYNC_CONCURRENCY)

    async def _fetch_achievements(
        app_id: int,
    ) -> SteamAchievementData:
        if achievements == "later":
            return {}, [], None
        async with semaphore:
            return await fetch_steam_achievements(steam_id, api_key, app_id)

    fetches = await asyncio.gather(
        *(
            _fetch_achievements(entry["appid"])
            for entry in owned_games
            if entry.get("appid") and entry.get("name")
        )
    )

    try:
        await steam_connection.recheck(db, current_user.id, steam_id, api_key)
    except steam_connection.SteamConnectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    index = await LibraryIndex.load(db, current_user.id, "Steam")
    # Steam's games response can omit demos, refunded apps and other licenses.
    # Absence cannot establish that a saved game is no longer owned. Repair old
    # omission-based warnings only after a valid response, scoped to active
    # Steam records for this user; retain all saved progress and relationships.
    for existing_games in index.by_title.values():
        for existing in existing_games:
            existing.stale_since = None
    games_added = games_updated = achievements_synced = 0
    newly_created: list[tuple[Game, int]] = []
    observations: list[tuple[UUID, SourceObservationV1 | None]] = []
    synced_titles: list[str] = []
    achievements_unavailable: list[str] = []
    achievement_ids: list[str] = []
    status_ids: list[str] = []
    fetch_index = 0
    for entry in owned_games:
        title, app_id = entry["name"], entry["appid"]
        schema, unlocked, descriptions = fetches[fetch_index]
        fetch_index += 1

        game, created = await _get_or_create_game(
            db, current_user.id, title, "Steam", external_id=str(app_id), index=index
        )
        achievement_ids.append(str(game.id))
        if "playtime_forever" in entry:
            game.playtime_seconds = entry["playtime_forever"] * 60
        if entry.get("rtime_last_played"):
            game.last_played_at = int(entry["rtime_last_played"])
        games_added += created
        games_updated += not created
        synced_titles.append(title)
        if game.status in {GameStatus.BACKLOG, GameStatus.WISHLIST}:
            status_ids.append(str(game.id))

        total_achievements = unlocked_count = 0
        if schema and unlocked is not None:
            rows = _steam_achievement_rows(schema, unlocked, None, descriptions)
            await _replace_achievements(db, game.id, "Steam", rows)
            achievements_synced += len(rows)
            total_achievements = len(rows)
            unlocked_count = sum(1 for r in rows if r["unlocked"])
        elif unlocked is None:
            achievements_unavailable.append(title)

        observations.append(
            (
                game.id,
                ingestion.build_observation(
                    str(app_id),
                    title=title,
                    provider_ids={"steam": str(app_id)},
                    owned=True,
                    playtime_seconds=entry["playtime_forever"] * 60
                    if "playtime_forever" in entry
                    else None,
                    last_played_at=int(entry["rtime_last_played"])
                    if entry.get("rtime_last_played")
                    else None,
                    achievements_total=total_achievements
                    if unlocked is not None and schema
                    else None,
                    achievements_unlocked=unlocked_count
                    if unlocked is not None and schema
                    else None,
                ),
            )
        )
        apply_provider_progress(
            game,
            owned=True,
            # Deferred achievement batches finish this progression with the saved
            # playtime: deciding Played before achievements arrive rules out Mastered.
            playtime_seconds=entry["playtime_forever"] * 60
            if "playtime_forever" in entry and achievements == "now"
            else None,
            total_achievements=total_achievements,
            unlocked_achievements=unlocked_count,
        )
        if created:
            _add_source_tag_and_collection(game, "Steam")
            newly_created.append((game, app_id))

    # Steam's response can omit demos and refunded licences, so absence never
    # marks a record unavailable (#72); only what was reported is recorded.
    await ingestion.record_observations(db, current_user.id, ingestion.STEAM, observations)
    current_user.steam_library_synced_at = int(time.time())
    await db.commit()
    return {
        "games_added": games_added,
        "games_updated": games_updated,
        "achievements_synced": achievements_synced,
        "achievements_unavailable": achievements_unavailable,
        "games_flagged_stale": 0,
        "games": synced_titles,
        # the new games still to be enriched (store details, tags, artwork), which
        # is slow, so the app asks for it in batches: see steam_import_steps.py
        "enrich_game_ids": [str(g.id) for g, _ in newly_created],
        "achievement_game_ids": achievement_ids if achievements == "later" else [],
        "status_game_ids": status_ids if achievements == "later" else [],
    }


# pylint: enable=duplicate-code,too-many-locals,too-many-statements


# Keep the established workflow and public parameters together.
# pylint: disable=too-many-locals,too-many-statements
@router.post("/retroachievements")
async def sync_retroachievements_library(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Serialize this account's manual and scheduled inventory pulls."""
    async with sync_scope(current_user.id, "retroachievements"):
        return await _sync_retroachievements_library(db, current_user)


async def _sync_retroachievements_library(db: AsyncSession, current_user: User) -> dict:
    if not current_user.retroachievements_username or not current_user.retroachievements_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Save your RetroAchievements username and API key first.",
        )

    scan_settings = await get_or_create_scan_settings(current_user.id, db)
    preferences = _scan_settings_to_preferences(scan_settings)
    client = RetroAchievementsClient(api_key=current_user.retroachievements_api_key)
    username = current_user.retroachievements_username

    try:
        owned_games = await asyncio.to_thread(client.get_user_games, username)
    except RetroAchievementsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    semaphore = asyncio.Semaphore(_SYNC_CONCURRENCY)

    async def _fetch_progress(game_id: str) -> dict | None:
        async with semaphore:
            try:
                return await asyncio.to_thread(client.get_game_progress, username, game_id)
            except RetroAchievementsError:
                return None

    game_ids = [str(entry.get("GameID")) for entry in owned_games if entry.get("GameID")]
    progress_results = await asyncio.gather(*(_fetch_progress(gid) for gid in game_ids))

    index = await LibraryIndex.load(db, current_user.id, "RetroAchievements")
    games_added = games_updated = achievements_synced = 0
    newly_created: list[Game] = []
    synced_titles: list[str] = []
    achievements_unavailable: list[str] = []
    touched_ids: set[UUID] = set()
    observations: list[tuple[UUID, SourceObservationV1 | None]] = []
    for entry, progress in zip(owned_games, progress_results, strict=False):
        title = entry.get("Title")
        if not title:
            continue
        game, created = await _get_or_create_game(
            db,
            current_user.id,
            title,
            "RetroAchievements",
            external_id=str(entry.get("GameID") or "") or None,
            index=index,
        )
        touched_ids.add(game.id)
        games_added += created
        games_updated += not created
        synced_titles.append(title)

        rows = _retro_achievement_rows(progress or {})
        if progress is None:
            achievements_unavailable.append(title)
        else:
            await _replace_achievements(db, game.id, "RetroAchievements", rows)
            achievements_synced += len(rows)
        observations.append(
            (
                game.id,
                ingestion.build_observation(
                    entry.get("GameID"),
                    title=title,
                    owned=True,
                    achievements_total=len(rows) if progress is not None else None,
                    achievements_unlocked=(
                        sum(1 for row in rows if row["unlocked"]) if progress is not None else None
                    ),
                ),
            )
        )

        apply_provider_progress(
            game,
            owned=True,
            total_achievements=len(rows),
            unlocked_achievements=sum(1 for row in rows if row["unlocked"]),
        )
        if created:
            _add_source_tag_and_collection(game, "RetroAchievements")
            newly_created.append(game)

    async def _enrich(game: Game) -> None:
        async with semaphore:
            await _enrich_new_game(game, current_user, preferences)

    await asyncio.gather(*(_enrich(g) for g in newly_created))

    games_flagged_stale = await _flag_stale_games(
        db, current_user.id, "RetroAchievements", touched_ids
    )
    await ingestion.record_observations(
        db, current_user.id, ingestion.RETROACHIEVEMENTS, observations
    )
    # The pull above completed, so the user's whole list was seen.
    await ingestion.record_complete_inventory(
        db,
        current_user.id,
        ingestion.RETROACHIEVEMENTS,
        {item.external_id for _, item in observations if item is not None},
    )
    current_user.retroachievements_library_synced_at = int(time.time())
    await db.commit()
    return {
        "games_added": games_added,
        "games_updated": games_updated,
        "achievements_synced": achievements_synced,
        "achievements_unavailable": achievements_unavailable,
        "games_flagged_stale": games_flagged_stale,
        "games": synced_titles,
    }


# pylint: enable=too-many-locals,too-many-statements


# Keep the established workflow and public parameters together.
# pylint: disable=too-many-locals,too-many-statements
@router.post("/psn")
async def sync_psn_library(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Serialize this account's manual and scheduled inventory pulls."""
    async with sync_scope(current_user.id, "psn"):
        return await _sync_psn_library(db, current_user)


async def _sync_psn_library(db: AsyncSession, current_user: User) -> dict:
    if not current_user.psn_npsso_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect your PlayStation account first.",
        )

    scan_settings = await get_or_create_scan_settings(current_user.id, db)
    preferences = _scan_settings_to_preferences(scan_settings)
    npsso = decrypt_secret(current_user.psn_npsso_token)
    client = PSNClient(npsso)

    try:
        titles = await asyncio.to_thread(client.get_owned_titles)
    except PSNError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # Validate title identities before pairing the fetched trophy snapshots with them.
    titles = [t for t in titles if t.get("trophyTitleName") and t.get("npCommunicationId")]
    semaphore = asyncio.Semaphore(_SYNC_CONCURRENCY)

    async def _fetch_trophies(np_communication_id: str, platform: str) -> list[dict] | None:
        async with semaphore:
            service_name = "trophy2" if "PS5" in (platform or "") else "trophy"
            try:
                return await asyncio.to_thread(
                    client.get_trophies_for_title, np_communication_id, service_name
                )
            except PSNError:
                return None

    trophy_results = await asyncio.gather(
        *(
            _fetch_trophies(t.get("npCommunicationId", ""), t.get("trophyTitlePlatform", ""))
            for t in titles
            if t.get("npCommunicationId")
        )
    )

    index = await LibraryIndex.load(db, current_user.id, "PlayStation")
    games_added = games_updated = achievements_synced = 0
    newly_created: list[Game] = []
    synced_titles: list[str] = []
    achievements_unavailable: list[str] = []
    touched_ids: set[UUID] = set()
    observations: list[tuple[UUID, SourceObservationV1 | None]] = []
    result_index = 0
    for entry in titles:
        title = entry["trophyTitleName"]
        trophies = trophy_results[result_index]
        result_index += 1

        game, created = await _get_or_create_game(
            db,
            current_user.id,
            title,
            "PlayStation",
            external_id=entry.get("npCommunicationId"),
            index=index,
        )
        touched_ids.add(game.id)
        games_added += created
        games_updated += not created
        synced_titles.append(title)

        rows = _psn_trophy_rows(trophies or [])
        if trophies is None:
            achievements_unavailable.append(title)
        else:
            await _replace_achievements(db, game.id, "PlayStation", rows)
            achievements_synced += len(rows)
        observations.append(
            (
                game.id,
                ingestion.build_observation(
                    entry.get("npCommunicationId"),
                    title=title,
                    platform=entry.get("trophyTitlePlatform"),
                    owned=True,
                    achievements_total=len(rows) if trophies is not None else None,
                    achievements_unlocked=(
                        sum(1 for row in rows if row["unlocked"]) if trophies is not None else None
                    ),
                ),
            )
        )

        apply_provider_progress(
            game,
            owned=True,
            total_achievements=len(rows),
            unlocked_achievements=sum(1 for row in rows if row["unlocked"]),
        )
        if created:
            _add_source_tag_and_collection(game, "PlayStation")
            newly_created.append(game)

    async def _enrich(game: Game) -> None:
        async with semaphore:
            await _enrich_new_game(game, current_user, preferences)

    await asyncio.gather(*(_enrich(g) for g in newly_created))

    games_flagged_stale = await _flag_stale_games(db, current_user.id, "PlayStation", touched_ids)
    await ingestion.record_observations(db, current_user.id, ingestion.PLAYSTATION, observations)
    await ingestion.record_complete_inventory(
        db,
        current_user.id,
        ingestion.PLAYSTATION,
        {item.external_id for _, item in observations if item is not None},
    )
    current_user.psn_library_synced_at = int(time.time())
    await db.commit()
    return {
        "games_added": games_added,
        "games_updated": games_updated,
        "achievements_synced": achievements_synced,
        "achievements_unavailable": achievements_unavailable,
        "games_flagged_stale": games_flagged_stale,
        "games": synced_titles,
    }


# pylint: enable=too-many-locals,too-many-statements
