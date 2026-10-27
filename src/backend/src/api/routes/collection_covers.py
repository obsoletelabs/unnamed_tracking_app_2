"""One cached cover image for each game collection overview tile."""

import asyncio
import hashlib
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.utils.collection_covers import collection_cover_response, store_cover_upload
from src.api.routes.utils.games import _DATA_ROOT
from src.core.auth import app_grant, get_current_user
from src.core.cache_metrics import cache_refresh, cache_request
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import get_db
from src.features import game_folders
from src.helpers.async_storage import finish_storage_operation
from src.helpers.collection_artwork import (
    render_cover as _render_cover,
)
from src.helpers.collection_artwork import (
    source_signature as _source_signature,
)
from src.helpers.save_game_asset import ASSET_FILENAMES

router = APIRouter(prefix="/api/game", tags=["game"], dependencies=[Depends(get_current_user)])


def _name_key(name: str) -> str:
    return hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()


def _cover_dir(user_id: UUID) -> Path:
    return _DATA_ROOT / str(user_id) / "collection-covers"


def _custom_path(user_id: UUID, name: str) -> Path:
    return _cover_dir(user_id) / f"{_name_key(name)}.webp"


async def _collection_rows(
    db: AsyncSession, user_id: UUID, chosen: list[UUID]
) -> list[tuple[UUID, str, Path]]:
    """Validate ownership and hydrate source storage before rendering a collage."""
    games = (
        (
            await db.scalars(
                select(Game)
                .where(
                    Game.user_id == user_id,
                    Game.deleted_at.is_(None),
                    Game.id.in_(chosen),
                )
                .with_for_update(read=True)
            )
        ).all()
        if chosen
        else []
    )
    by_id = {game.id: game for game in games}
    if len(by_id) != len(chosen):
        raise HTTPException(404, "A collection cover game was not found.")
    try:
        for game in games:
            await finish_storage_operation(
                asyncio.to_thread(game_folders.ensure_storage_ready, _DATA_ROOT, game)
            )
    except game_folders.FolderStorageError as exc:
        raise HTTPException(409, str(exc)) from exc
    return [
        (
            game_id,
            by_id[game_id].title,
            _DATA_ROOT
            / str(user_id)
            / "games"
            / by_id[game_id].folder_location
            / ASSET_FILENAMES["key_art"],
        )
        for game_id in chosen
    ]


@router.get("/collection-cover")
@app_grant("library.sync")
async def get_collection_cover(
    request: Request,
    *,
    name: Annotated[str, Query(min_length=1, max_length=200)],
    ids: Annotated[list[UUID] | None, Query()] = None,
    owner: Annotated[UUID | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Serve a user upload or a cached collage of the ordered first four games."""
    if owner is not None and owner != current_user.id:
        raise HTTPException(403, "This collection cover belongs to another account.")
    cache_control = None if owner is not None else "private, no-cache"
    chosen = ids or []
    if len(chosen) > 4 or len(set(chosen)) != len(chosen):
        raise HTTPException(400, "Choose up to four distinct games.")
    custom = _custom_path(current_user.id, name)
    if await asyncio.to_thread(custom.is_file):
        cache_request("collection_cover", "bypass")
        return await collection_cover_response(request, custom, cache_control)

    rows = await _collection_rows(db, current_user.id, chosen)
    signature = await asyncio.to_thread(_source_signature, rows)
    key = hashlib.sha256(f"v1:{name}:{signature}".encode("utf-8")).hexdigest()
    destination = _cover_dir(current_user.id) / "cache" / f"{key}.webp"
    if not await asyncio.to_thread(destination.is_file):
        cache_request("collection_cover", "miss")
        with cache_refresh("collection_cover"):
            await finish_storage_operation(
                asyncio.to_thread(_render_cover, rows, name, destination)
            )
    else:
        cache_request("collection_cover", "hit")
    return await collection_cover_response(request, destination, cache_control)


@router.put("/collection-cover")
@app_grant("library.sync")
async def upload_collection_cover(
    name: Annotated[str, Form(min_length=1, max_length=200)],
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    return await store_cover_upload(file, _custom_path(current_user.id, name))


@router.delete("/collection-cover")
@app_grant("library.sync")
async def remove_collection_cover(
    name: Annotated[str, Query(min_length=1, max_length=200)],
    current_user: User = Depends(get_current_user),
) -> Response:
    await finish_storage_operation(
        asyncio.to_thread(_custom_path(current_user.id, name).unlink, missing_ok=True)
    )
    return Response(status_code=204)


class CoverRename(BaseModel):
    old_name: str = Field(min_length=1, max_length=200)
    new_name: str = Field(min_length=1, max_length=200)


@router.post("/collection-cover/rename")
@app_grant("library.sync")
async def rename_collection_cover(
    payload: CoverRename,
    current_user: User = Depends(get_current_user),
) -> Response:
    old_path = _custom_path(current_user.id, payload.old_name)
    new_path = _custom_path(current_user.id, payload.new_name)
    if old_path != new_path and await asyncio.to_thread(old_path.is_file):
        if await asyncio.to_thread(new_path.exists):
            raise HTTPException(409, "The new collection already has a custom cover.")
        await finish_storage_operation(asyncio.to_thread(old_path.replace, new_path))
    return Response(status_code=204)
