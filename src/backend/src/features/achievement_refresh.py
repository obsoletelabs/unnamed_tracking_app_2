"""Refresh platform progress and merge achievement catalogues into a game."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import decrypt_secret
from src.database.models.achievement import Achievement
from src.database.models.achievement_override import AchievementOverride
from src.database.models.game import Game
from src.database.models.user import User
from src.features.achievement_catalogue import store_catalogue
from src.features.achievement_folding import apply_manual_progress, fold_catalog
from src.features.achievement_sync import (
    psn_trophy_rows,
    replace_achievements,
    retro_achievement_rows,
)
from src.features.imports import steam_connection
from src.features.imports.game_progression import apply_provider_progress
from src.features.imports.steam_achievements import fetch_steam_achievements
from src.features.metadata.games import steam
from src.features.metadata.games.psn import PSNClient, PSNError
from src.features.metadata.games.retroachievements import (
    RetroAchievementsClient,
    RetroAchievementsError,
)
from src.helpers.steam_achievement_rows import steam_achievement_rows

ProgressFetcher = Callable[..., Awaitable[list[dict]]]
CatalogueFetcher = Callable[..., Awaitable[tuple[str, list[dict], list[str]]]]


async def fetch_steam_rows(user: User, external_id: str, db: AsyncSession) -> list[dict]:
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
    return steam_achievement_rows(schema, unlocked, percentages, descriptions)


async def fetch_retro_rows(user: User, external_id: str) -> list[dict]:
    if not user.retroachievements_username or not user.retroachievements_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Save your RetroAchievements username and API key first.",
        )
    if not external_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This game has no RetroAchievements id.",
        )
    client = RetroAchievementsClient(api_key=user.retroachievements_api_key)
    try:
        progress = await asyncio.to_thread(
            client.get_game_progress, user.retroachievements_username, external_id
        )
    except RetroAchievementsError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return retro_achievement_rows(progress)


async def fetch_psn_rows(
    user: User,
    external_id: str,
    platform: str | None,
    *,
    secret_decryptor: Callable[[str], str] = decrypt_secret,
    client_factory: Callable[[str], PSNClient] = PSNClient,
) -> list[dict]:
    if not user.psn_npsso_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect your PlayStation account first.",
        )
    if not external_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This game has no PlayStation id.",
        )
    client = client_factory(secret_decryptor(user.psn_npsso_token))
    services = ["trophy2", "trophy"] if "PS5" in (platform or "") else ["trophy", "trophy2"]
    try:
        for service_name in services:
            trophies = await asyncio.to_thread(
                client.get_trophies_for_title, external_id, service_name
            )
            if trophies:
                return psn_trophy_rows(trophies)
    except PSNError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return []


@dataclasses.dataclass(frozen=True)
class AchievementRefreshSources:
    fetch_steam_rows: ProgressFetcher
    fetch_retro_rows: ProgressFetcher
    fetch_psn_rows: ProgressFetcher
    fetch_catalogue: CatalogueFetcher


@dataclasses.dataclass(frozen=True)
class _GameRefreshIdentity:
    source: str | None
    external_id: str
    platform: str | None
    provider_ids: dict


@dataclasses.dataclass(frozen=True)
class _AchievementRefreshData:
    progress_provider: str | None
    progress_rows: list[dict]
    progress_error: HTTPException | None
    catalogue_provider: str
    catalogue_rows: list[dict]
    catalogue_errors: list[str]


async def refresh_game_achievements(
    game_id: UUID,
    db: AsyncSession,
    current_user: User,
    sources: AchievementRefreshSources,
) -> dict:
    """Import metadata definitions alongside platform progress for one game."""
    game = await _get_refresh_game(db, game_id, current_user.id)
    identity = _snapshot_game_identity(game)
    progress_provider, progress_rows, progress_error = await _fetch_platform_progress(
        current_user, game, identity.external_id, db, sources
    )
    # Platform data supplies unlock state; metadata providers add definitions and detail.
    catalogue_provider, catalogue_rows, catalogue_errors = await sources.fetch_catalogue(
        db, current_user.id, game
    )
    refresh_data = _AchievementRefreshData(
        progress_provider=progress_provider,
        progress_rows=progress_rows,
        progress_error=progress_error,
        catalogue_provider=catalogue_provider,
        catalogue_rows=catalogue_rows,
        catalogue_errors=catalogue_errors,
    )
    await _save_achievement_refresh(db, current_user, game_id, identity, refresh_data)
    return await _achievement_refresh_result(db, game_id, refresh_data)


def _snapshot_game_identity(game: Game) -> _GameRefreshIdentity:
    return _GameRefreshIdentity(
        source=game.source,
        external_id=game.external_id or "",
        platform=game.platform,
        provider_ids=dict(game.provider_ids or {}),
    )


async def _get_refresh_game(db: AsyncSession, game_id: UUID, user_id: UUID) -> Game:
    game = await db.scalar(
        select(Game).where(Game.id == game_id, Game.user_id == user_id, Game.deleted_at.is_(None))
    )
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    return game


async def _fetch_platform_progress(
    user: User,
    game: Game,
    external_id: str,
    db: AsyncSession,
    sources: AchievementRefreshSources,
) -> tuple[str | None, list[dict], HTTPException | None]:
    provider: str | None = None
    try:
        if game.source == "Steam":
            provider = "Steam"
            rows = await sources.fetch_steam_rows(user, external_id, db)
        elif game.source == "RetroAchievements":
            provider = "RetroAchievements"
            rows = await sources.fetch_retro_rows(user, external_id)
        elif game.source == "PlayStation":
            provider = "PlayStation"
            rows = await sources.fetch_psn_rows(user, external_id, game.platform)
        else:
            rows = []
        return provider, rows, None
    except HTTPException as exc:
        return provider, [], exc


async def _raise_empty_refresh_error(
    db: AsyncSession,
    game_id: UUID,
    progress_error: HTTPException | None,
    catalogue_errors: list[str],
) -> None:
    existing_rows = (
        await db.scalars(select(Achievement).where(Achievement.game_id == game_id))
    ).all()
    if existing_rows:
        return
    if progress_error is not None:
        raise progress_error
    if catalogue_errors:
        detail = "Metadata provider lookup failed: " + ", ".join(catalogue_errors[:3])
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)
    # No match is a successful check during an all-library import.


async def _save_achievement_refresh(
    db: AsyncSession,
    current_user: User,
    game_id: UUID,
    identity: _GameRefreshIdentity,
    data: _AchievementRefreshData,
) -> None:
    if not data.progress_rows and not data.catalogue_rows:
        await _raise_empty_refresh_error(db, game_id, data.progress_error, data.catalogue_errors)
        return
    current = await db.scalar(
        select(Game)
        .where(
            Game.id == game_id,
            Game.user_id == current_user.id,
            Game.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        current is None
        or current.source != identity.source
        or (current.external_id or "") != identity.external_id
        or current.platform != identity.platform
        or dict(current.provider_ids or {}) != identity.provider_ids
    ):
        raise HTTPException(
            status_code=409,
            detail="This game changed during the refresh. Saved progress was kept.",
        )
    if data.progress_rows and data.progress_provider:
        await replace_achievements(db, current.id, data.progress_provider, data.progress_rows)
        apply_provider_progress(
            current,
            total_achievements=len(data.progress_rows),
            unlocked_achievements=sum(1 for row in data.progress_rows if row["unlocked"]),
        )
    if data.catalogue_rows:
        await store_catalogue(db, current.id, data.catalogue_provider, data.catalogue_rows)
    await db.commit()


async def _achievement_refresh_result(
    db: AsyncSession,
    game_id: UUID,
    data: _AchievementRefreshData,
) -> dict:
    saved_rows = list(
        (await db.scalars(select(Achievement).where(Achievement.game_id == game_id))).all()
    )
    overrides = (
        await db.scalars(select(AchievementOverride).where(AchievementOverride.game_id == game_id))
    ).all()
    merged = fold_catalog(apply_manual_progress(saved_rows, overrides))
    warnings: list[str] = []
    if data.progress_error is not None:
        warnings.append(
            f"{data.progress_provider} progress was unavailable: {data.progress_error.detail}"
        )
    if data.catalogue_errors:
        warnings.append(
            "Metadata provider lookup had errors: " + ", ".join(data.catalogue_errors[:3])
        )

    providers = []
    if data.progress_rows and data.progress_provider:
        providers.append(data.progress_provider)
    if data.catalogue_rows:
        providers.append(data.catalogue_provider)
    result = {
        "provider": " + ".join(providers) if providers else "No new data",
        "achievements": len(merged),
        "unlocked": sum(1 for row, _entry in merged if row.unlocked),
        "hidden": sum(
            1
            for row, entry in merged
            if not row.unlocked and (row.hidden or (entry is not None and entry.hidden))
        ),
    }
    if warnings:
        result["warnings"] = warnings
    return result
