"""The slow parts of a Steam import, done a few games at a time.

The frontend saves owned games first, then asks for achievements and enrichment
in small batches. Failed provider reads leave the saved games and unavailable
achievement snapshots intact.
"""

import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.library_sync import (
    _SYNC_CONCURRENCY,
    _add_source_tag_and_collection,
    _apply_status,
    _enrich_steam_game_by_appid,
    _get_or_create_game,
    _infer_status,
    _replace_achievements,
)
from src.core.auth import get_current_user
from src.core.preferences import load_preferences
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import get_db
from src.features.imports.library_games import LibraryIndex
from src.features.imports.steam_achievements import SteamAchievementData, fetch_steam_achievements
from src.features.metadata.games import steam, steam_wishlist
from src.helpers.steam_achievement_rows import steam_achievement_rows

router = APIRouter(
    prefix="/api/library-sync/steam",
    tags=["library-sync"],
    dependencies=[Depends(get_current_user)],
)

_PLACEHOLDER = "Steam app "


class EnrichRequest(BaseModel):
    game_ids: list[UUID] = Field(max_length=25)


class AchievementsRequest(EnrichRequest):
    status_game_ids: list[UUID] = Field(default_factory=list, max_length=25)


@router.post("/achievements")
async def import_achievements(
    body: AchievementsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Fetch at most 25 owned Steam games, then lock and recheck before saving."""
    api_key = current_user.steam_api_key
    if not current_user.steam_id or not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Save your Steam ID and API key first."
        )
    try:
        steam_id = await asyncio.to_thread(steam.resolve_steam_id, current_user.steam_id, api_key)
    except steam.SteamLibraryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    scope = (
        Game.id.in_(body.game_ids),
        Game.user_id == current_user.id,
        Game.source == "Steam",
        Game.deleted_at.is_(None),
    )
    references = (await db.execute(select(Game.id, Game.external_id).where(*scope))).all()
    semaphore = asyncio.Semaphore(_SYNC_CONCURRENCY)

    async def fetch(app_id: str | None) -> SteamAchievementData:
        if not app_id or not app_id.isascii() or not app_id.isdigit():
            return {}, None, None
        async with semaphore:
            return await fetch_steam_achievements(steam_id, api_key, int(app_id))

    snapshots = await asyncio.gather(*(fetch(reference.external_id) for reference in references))
    fetched = {
        reference.id: (reference.external_id, data)
        for reference, data in zip(references, snapshots, strict=True)
    }
    return await _save_achievement_batch(db, current_user.id, fetched, body.status_game_ids)


async def _save_achievement_batch(
    db: AsyncSession,
    user_id: UUID,
    fetched: dict[UUID, tuple[str | None, SteamAchievementData]],
    status_game_ids: list[UUID],
) -> dict[str, Any]:
    """Apply snapshots only to identities still owned when the provider responds."""
    games = (
        await db.scalars(
            select(Game)
            .where(
                Game.id.in_(fetched),
                Game.user_id == user_id,
                Game.source == "Steam",
                Game.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    settle = set(status_game_ids)
    synced = 0
    unavailable: list[str] = []
    for game in games:
        snapshot = fetched.get(game.id)
        if snapshot is None or snapshot[0] != game.external_id:
            continue
        schema, unlocked, descriptions = snapshot[1]
        if unlocked is None:
            unavailable.append(game.title)
            continue
        rows = steam_achievement_rows(schema, unlocked, None, descriptions)
        if schema:
            await _replace_achievements(db, game.id, "Steam", rows)
            synced += len(rows)
        # Only the fresh imports identified by the original sync may settle
        # their automatic status. A subsequent manual choice is retained.
        if game.id in settle and game.status == _infer_status(
            playtime_seconds=game.playtime_seconds
        ):
            _apply_status(
                game,
                _infer_status(
                    playtime_seconds=game.playtime_seconds,
                    total_achievements=len(rows),
                    unlocked_achievements=sum(1 for row in rows if row["unlocked"]),
                ),
            )
    await db.commit()
    return {"achievements_synced": synced, "achievements_unavailable": unavailable}


@router.post("/wishlist")
async def import_wishlist(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Add the Steam wishlist as Wishlist games, when the setting is on. The
    names come later, from the same enrich step as every other new game."""
    if not (await load_preferences(db, current_user.id))["steam_import_wishlist"]:
        return {"enabled": False, "added": 0, "game_ids": []}
    if not current_user.steam_id or not current_user.steam_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Save your Steam ID and API key first."
        )
    try:
        steam_id = await asyncio.to_thread(
            steam.resolve_steam_id, current_user.steam_id, current_user.steam_api_key
        )
        app_ids = await asyncio.to_thread(
            steam_wishlist.get_wishlist_app_ids, steam_id, current_user.steam_api_key
        )
    except steam.SteamLibraryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    index = await LibraryIndex.load(db, current_user.id, "Steam")
    game_ids: list[str] = []
    for app_id in app_ids:
        game, created = await _get_or_create_game(
            db,
            current_user.id,
            f"{_PLACEHOLDER}{app_id}",
            "Steam",
            external_id=str(app_id),
            index=index,
        )
        if created:
            game.status = GameStatus.WISHLIST
            _add_source_tag_and_collection(game, "Steam")
            await db.flush()
            game_ids.append(str(game.id))
    await db.commit()
    return {
        "enabled": True,
        "wishlisted": len(app_ids),
        "added": len(game_ids),
        "game_ids": game_ids,
    }


@router.post("/enrich")
async def enrich_games(
    body: EnrichRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int]:
    """Fill in the store details, tags, series and artwork of up to 25 of the
    user's Steam games."""
    game_ids = (
        await db.scalars(
            select(Game.id).where(
                Game.id.in_(body.game_ids),
                Game.user_id == current_user.id,
                Game.source == "Steam",
                Game.deleted_at.is_(None),
            )
        )
    ).all()
    use_tags = (await load_preferences(db, current_user.id))["steam_user_tags"]
    enriched = failed = 0
    for game_id in game_ids:
        try:
            async with db.begin_nested():
                game = await db.scalar(
                    select(Game)
                    .where(
                        Game.id == game_id,
                        Game.user_id == current_user.id,
                        Game.source == "Steam",
                        Game.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
                if game is None:
                    continue
                app_id = int(game.external_id or "")
                await _enrich_steam_game_by_appid(
                    game,
                    app_id,
                    current_user,
                    use_tags,
                )
            enriched += 1
        except Exception:  # pylint: disable=broad-exception-caught
            failed += 1
        await db.commit()
    return {"enriched": enriched, "failed": failed}
