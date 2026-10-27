"""Shared HTTP upload and cache semantics for game and media collection covers."""

import asyncio
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError

from src.helpers.artwork_revisions import artwork_response, stored_file_revision
from src.helpers.async_storage import finish_storage_operation
from src.helpers.collection_artwork import save_custom_cover

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


async def collection_cover_response(
    request: Request, path: Path, cache_control: str | None
) -> Response:
    revision = await stored_file_revision(path)
    return artwork_response(
        request,
        revision=revision,
        source_revision=revision,
        version=None,
        media_type="image/webp",
        path=path,
        cache_control=cache_control,
    )


async def store_cover_upload(file: UploadFile, destination: Path) -> dict[str, str]:
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Collection cover must be 10 MB or smaller.")
    if not data:
        raise HTTPException(400, "Collection cover is empty.")
    try:
        await finish_storage_operation(asyncio.to_thread(save_custom_cover, data, destination))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(
            400, "File must be a valid image no larger than 25 megapixels."
        ) from exc
    return {"revision": await stored_file_revision(destination)}
