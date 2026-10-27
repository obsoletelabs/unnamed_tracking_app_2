"""One cached cover image for each game collection overview tile."""

import asyncio
import hashlib
import json
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.utils.games import _DATA_ROOT
from src.core.auth import app_grant, get_current_user
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import get_db
from src.features import game_folders
from src.helpers.artwork_revisions import artwork_response, stored_file_revision
from src.helpers.async_storage import finish_storage_operation
from src.helpers.game_cover_placeholder import cover_palette
from src.helpers.save_game_asset import ASSET_FILENAMES

router = APIRouter(prefix="/api/game", tags=["game"], dependencies=[Depends(get_current_user)])
_COVER_SIZE = (400, 600)
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _name_key(name: str) -> str:
    return hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()


def _cover_dir(user_id: UUID) -> Path:
    return _DATA_ROOT / str(user_id) / "collection-covers"


def _custom_path(user_id: UUID, name: str) -> Path:
    return _cover_dir(user_id) / f"{_name_key(name)}.webp"


def _placeholder(canvas: Image.Image, seed: str, box: tuple[int, int, int, int]) -> None:
    """Match the library's stable gamepad and palette without an image request."""
    background, foreground, _accent = cover_palette(seed)
    _placeholder_gradient(canvas, background, foreground, box)
    _placeholder_gamepad(canvas, box)


def _placeholder_gradient(
    canvas: Image.Image, background: str, foreground: str, box: tuple[int, int, int, int]
) -> None:
    left, top, right, bottom = box
    draw = ImageDraw.Draw(canvas)
    height = bottom - top
    start = tuple(int(foreground[i : i + 2], 16) for i in (1, 3, 5))
    end = tuple(int(background[i : i + 2], 16) for i in (1, 3, 5))
    for y in range(top, bottom):
        fraction = (y - top) / max(1, height - 1)
        color = tuple(
            round(a * (1 - fraction) + b * fraction) for a, b in zip(start, end, strict=True)
        )
        draw.line((left, y, right, y), fill=color)


_GAMEPAD_OUTLINE = (
    (17, 24),
    (47, 24),
    (55, 30),
    (59, 48),
    (52, 54),
    (44, 46),
    (20, 46),
    (12, 54),
    (5, 48),
    (9, 30),
    (17, 24),
)


def _placeholder_gamepad(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(canvas)
    scale = 0.54 * min((box[2] - box[0]) / 64, (box[3] - box[1]) / 64)
    center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    def point(x: float, y: float) -> tuple[int, int]:
        return round(center[0] + (x - 32) * scale), round(center[1] + (y - 32) * scale)

    stroke = max(2, round(3 * scale))
    draw.line(
        [point(x, y) for x, y in _GAMEPAD_OUTLINE], fill="#aeb7c2", width=stroke, joint="curve"
    )
    for line in (((19, 31), (19, 43)), ((13, 37), (25, 37))):
        draw.line((point(*line[0]), point(*line[1])), fill="#aeb7c2", width=stroke)
    for x, y in ((42, 34), (49, 39)):
        px, py = point(x, y)
        draw.ellipse((px - stroke, py - stroke, px + stroke, py + stroke), fill="#aeb7c2")


def _source_signature(rows: list[tuple[UUID, str, Path]]) -> str:
    parts = []
    for game_id, title, path in rows:
        try:
            info = path.stat()
            signature = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            signature = None
        parts.append((str(game_id), title, signature))
    return hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()


def _save_webp(image: Image.Image, destination: Path, quality: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
    os.close(handle)
    try:
        image.save(temporary, format="WEBP", quality=quality, method=4)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _render_cover(rows: list[tuple[UUID, str, Path]], name: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", _COVER_SIZE)
    available = [path.is_file() for _, _, path in rows]
    if not any(available):
        _placeholder(canvas, name, (0, 0, *_COVER_SIZE))
    else:
        count = len(rows)
        if count == 1:
            boxes = [(0, 0, *_COVER_SIZE)]
        elif count == 2:
            boxes = [(0, 0, 198, 600), (202, 0, 400, 600)]
        else:
            boxes = [(0, 0, 198, 298), (202, 0, 400, 298), (0, 302, 198, 600), (202, 302, 400, 600)]
        for (game_id, _title, path), box in zip(rows, boxes, strict=False):
            if not path.is_file():
                _placeholder(canvas, str(game_id), box)
                continue
            try:
                with Image.open(path) as source:
                    tile = ImageOps.fit(source.convert("RGB"), (box[2] - box[0], box[3] - box[1]))
                    canvas.paste(tile, box[:2])
            except (UnidentifiedImageError, OSError):
                _placeholder(canvas, str(game_id), box)
        if count == 3:
            _placeholder(canvas, name, boxes[3])
    _save_webp(canvas, destination, 82)
    canvas.close()


def _save_custom(data: bytes, destination: Path) -> None:
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 25_000_000:
            raise ValueError("Collection cover is too large.")
        source.load()
        image = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), _COVER_SIZE)
    _save_webp(image, destination, 85)
    image.close()


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
        revision = await stored_file_revision(custom)
        return artwork_response(
            request,
            revision=revision,
            source_revision=revision,
            version=None,
            media_type="image/webp",
            path=custom,
            cache_control=cache_control,
        )

    rows = await _collection_rows(db, current_user.id, chosen)
    signature = await asyncio.to_thread(_source_signature, rows)
    key = hashlib.sha256(f"v1:{name}:{signature}".encode("utf-8")).hexdigest()
    destination = _cover_dir(current_user.id) / "cache" / f"{key}.webp"
    if not await asyncio.to_thread(destination.is_file):
        await finish_storage_operation(asyncio.to_thread(_render_cover, rows, name, destination))
    revision = await stored_file_revision(destination)
    return artwork_response(
        request,
        revision=revision,
        source_revision=revision,
        version=None,
        media_type="image/webp",
        path=destination,
        cache_control=cache_control,
    )


@router.put("/collection-cover")
@app_grant("library.sync")
async def upload_collection_cover(
    name: Annotated[str, Form(min_length=1, max_length=200)],
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Collection cover must be 10 MB or smaller.")
    if not data:
        raise HTTPException(400, "Collection cover is empty.")
    try:
        destination = _custom_path(current_user.id, name)
        await finish_storage_operation(asyncio.to_thread(_save_custom, data, destination))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(
            400, "File must be a valid image no larger than 25 megapixels."
        ) from exc
    return {"revision": await stored_file_revision(destination)}


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
