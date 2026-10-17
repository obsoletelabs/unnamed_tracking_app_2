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
import re
import time
from datetime import UTC, date, datetime
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
from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal, get_db
from src.features.connection_jobs import sync_scope
from src.features.game_identity import names
from src.features.game_ingestion import legacy as ingestion
from src.features.game_ingestion.contract import SourceObservationV1
from src.features.imports import steam_connection
from src.features.imports.game_progression import apply_provider_progress
from src.features.imports.library_games import LibraryIndex, lock_imported_game
from src.features.imports.library_games import get_or_create_game as _get_or_create_game
from src.features.imports.steam_achievements import SteamAchievementData, fetch_steam_achievements
from src.features.metadata.games import steam
from src.features.metadata.games.psn import PSNClient, PSNError
from src.features.metadata.games.retroachievements import (
    RetroAchievementsClient,
    RetroAchievementsError,
)
from src.features.metadata.locked_fields import apply_metadata_updates
from src.features.metadata.service import game_result, library_candidate, resolve_library_record
from src.helpers.remote_images import MAX_BYTES, RemoteImageError, download_image
from src.helpers.save_game_asset import AssetKind, save_locked_game_asset
from src.helpers.steam_achievement_rows import (  # noqa: F401
    needs_community_descriptions as _needs_community_descriptions,
)
from src.helpers.steam_achievement_rows import (
    steam_achievement_rows as _steam_achievement_rows,
)
from src.plugin_api.metadata_contracts import MediaType

router = APIRouter(
    prefix="/api/library-sync", tags=["library-sync"], dependencies=[Depends(get_current_user)]
)

_SYNC_CONCURRENCY = 5

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
) -> None:
    """Owned-game IDs anchor enrichment without another title match."""
    game.provider_ids = {**(game.provider_ids or {}), "steam": str(app_id)}
    await _enrich_new_game(game, user, {"steam_user_tags": use_user_tags})


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


async def _enrich_new_game(game: Game, user: User, preferences: dict) -> None:
    """Fill newly imported rows through the same scoped metadata and media operations."""
    candidate = library_candidate(
        game.title,
        MediaType.GAME,
        game.provider_ids or {},
        game.release_date.year if game.release_date else None,
    )
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
        return
    match = game_result(record)
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
    asset_fields: list[tuple[AssetKind, str]] = [
        ("key_art", "key_art_url"),
        ("banner", "banner_url"),
        ("logo", "logo_url"),
        ("icon", "icon_url"),
    ]
    for asset_kind, field in asset_fields:
        for url in match.get(field + "s", []):
            if await _download_asset(url, game, asset_kind, max_bytes=max_image_bytes):
                break


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


def _unix_from_text(text: object) -> int | None:
    """A provider's timestamp text ("2023-05-02 14:03:00" or an ISO string
    ending in Z) as unix seconds, or None when it's missing or unreadable."""
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def _retro_achievement_rows(progress: dict) -> list[dict]:
    """Stored rows for one RetroAchievements game. `NumAwarded` over the
    game's player count is the share of players who earned each one."""
    players = progress.get("NumDistinctPlayers") or progress.get("NumDistinctPlayersCasual")
    rows = []
    for ach_id, ach in (progress.get("Achievements") or {}).items():
        earned = ach.get("DateEarned") or ach.get("DateEarnedHardcore")
        awarded = ach.get("NumAwarded")
        row = {
            "external_id": str(ach_id),
            "name": ach.get("Title") or str(ach_id),
            "description": ach.get("Description"),
            "icon_url": f"https://media.retroachievements.org/Badge/{ach['BadgeName']}.png"
            if ach.get("BadgeName")
            else None,
            "unlocked": bool(earned),
            "unlocked_at": _unix_from_text(earned),
            # RA's classic API's exact casing for this field isn't documented
            # as clearly as the newer v1 API's: check both
            "tier": (ach.get("Type") or ach.get("type") or "").lower() or None,
        }
        if isinstance(awarded, int | float) and isinstance(players, int | float) and players > 0:
            row["global_percent"] = round(awarded / players * 100, 2)
        rows.append(row)
    return rows


def _psn_trophy_rows(trophies: list[dict]) -> list[dict]:
    """Stored rows for one PlayStation title: earned state and time, whether
    it's a hidden trophy, and the rate of players who earned it."""
    rows = []
    for t in trophies:
        if t.get("trophyId") is None:
            continue
        row = {
            "external_id": str(t.get("trophyId")),
            "name": t.get("trophyName") or "Trophy",
            "description": t.get("trophyDetail"),
            "icon_url": t.get("trophyIconUrl"),
            "unlocked": bool(t.get("earned")),
            "unlocked_at": _unix_from_text(t.get("earnedDateTime")),
            "hidden": bool(t.get("trophyHidden")),
        }
        try:
            row["global_percent"] = round(float(t["trophyEarnedRate"]), 2)
        except (KeyError, TypeError, ValueError):
            pass
        rows.append(row)
    return rows


async def _replace_achievements(
    db: AsyncSession, game_id: UUID, provider: str, rows: list[dict]
) -> None:
    """Upserts by (game_id, provider, external_id) rather than delete +
    reinsert — a media item can link to a specific achievement
    (MediaItem.linked_achievement_id), and that FK is ON DELETE SET NULL,
    so wiping every row on each re-sync was silently unlinking every
    screenshot/clip tagged to an achievement the next time the game synced."""
    existing = {
        a.external_id: a
        for a in (
            await db.execute(
                select(Achievement).where(
                    Achievement.game_id == game_id, Achievement.provider == provider
                )
            )
        )
        .scalars()
        .all()
    }
    now = int(time.time())
    seen_ids: set[str] = set()
    for row in rows:
        seen_ids.add(row["external_id"])
        current = existing.get(row["external_id"])
        if current is not None:
            for field, value in row.items():
                # a lookup that found no description (Steam refused the extra
                # request, say) must not erase one we already have
                if field == "description" and value is None and current.description:
                    continue
                setattr(current, field, value)
        else:
            db.add(Achievement(game_id=game_id, provider=provider, created_at=now, **row))
    for external_id, achievement in existing.items():
        if external_id not in seen_ids:
            await db.delete(achievement)


@router.post("/games/{game_id}/achievements")
async def refresh_game_achievements(
    game_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Re-reads one game's achievements from the provider it came from (Steam,
    PlayStation or RetroAchievements): what each is, whether it's hidden, what
    you've unlocked and when, and how many players have it. It doesn't sync the
    library. Fresh progress may advance eligible Backlog games; other saved
    statuses, including Wishlist without ownership evidence, are preserved."""
    game = await db.scalar(
        select(Game).where(
            Game.id == game_id, Game.user_id == current_user.id, Game.deleted_at.is_(None)
        )
    )
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    external_id = game.external_id or ""
    platform = game.platform
    if game.source == "Steam":
        provider, rows = "Steam", await _fetch_steam_rows(current_user, external_id, db)
    elif game.source == "RetroAchievements":
        provider, rows = "RetroAchievements", await _fetch_retro_rows(current_user, external_id)
    elif game.source == "PlayStation":
        provider = "PlayStation"
        rows = await _fetch_psn_rows(current_user, external_id, game.platform)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only games that came from Steam, PlayStation or RetroAchievements have "
            "achievements to refresh.",
        )
    if rows:
        # Every provider must recheck ownership and identity after network work.
        current = await lock_imported_game(db, current_user.id, game_id, provider)
        if (
            current is None
            or (current.external_id or "") != external_id
            or (provider == "PlayStation" and current.platform != platform)
        ):
            raise HTTPException(
                status_code=409,
                detail="This game changed during the refresh. Saved progress was kept.",
            )
        await _replace_achievements(db, current.id, provider, rows)
        apply_provider_progress(
            current,
            total_achievements=len(rows),
            unlocked_achievements=sum(1 for row in rows if row["unlocked"]),
        )
        await db.commit()
    return {
        "provider": provider,
        "achievements": len(rows),
        "unlocked": sum(1 for r in rows if r["unlocked"]),
        "hidden": sum(1 for r in rows if r.get("hidden")),
    }


async def _fetch_steam_rows(user: User, external_id: str, db: AsyncSession) -> list[dict]:
    if not external_id.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This game has no Steam app id."
        )
    app_id = int(external_id)
    try:
        steam_id, api_key = await steam_connection.credentials(db, user)
        schema, unlocked, descriptions = await fetch_steam_achievements(steam_id, api_key, app_id)
        percentages = (
            await asyncio.to_thread(steam.get_global_percentages, app_id)
            if schema and unlocked is not None
            else None
        )
        await steam_connection.recheck(db, user.id, steam_id, api_key)
    except steam_connection.SteamConnectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    except steam.SteamLibraryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
    if unlocked is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Steam achievement progress was unavailable. Saved progress was kept. "
            "Reconnect Steam in Connections if its session has expired.",
        )
    if not schema:
        return []
    return _steam_achievement_rows(schema, unlocked, percentages, descriptions)


async def _fetch_retro_rows(user: User, external_id: str) -> list[dict]:
    if not user.retroachievements_username or not user.retroachievements_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Save your RetroAchievements username and API key first.",
        )
    if not external_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This game has no RetroAchievements id."
        )
    client = RetroAchievementsClient(api_key=user.retroachievements_api_key)
    try:
        progress = await asyncio.to_thread(
            client.get_game_progress, user.retroachievements_username, external_id
        )
    except RetroAchievementsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _retro_achievement_rows(progress)


async def _fetch_psn_rows(user: User, external_id: str, platform: str | None) -> list[dict]:
    if not user.psn_npsso_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect your PlayStation account first.",
        )
    if not external_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This game has no PlayStation id."
        )
    client = PSNClient(decrypt_secret(user.psn_npsso_token))
    # PS5 titles use the newer trophy service, so ask it first for those and
    # fall back to the other when a title comes back empty
    services = ["trophy2", "trophy"] if "PS5" in (platform or "") else ["trophy", "trophy2"]
    try:
        for service_name in services:
            trophies = await asyncio.to_thread(
                client.get_trophies_for_title, external_id, service_name
            )
            if trophies:
                return _psn_trophy_rows(trophies)
    except PSNError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return []


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
