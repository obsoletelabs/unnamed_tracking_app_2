"""One account-owned, cached cover for a movie/TV/anime collection tile."""

import asyncio
import hashlib
import time
from collections import OrderedDict
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.media_lists import _get_list_or_404, _resolve_items
from src.api.routes.utils.collection_covers import collection_cover_response, store_cover_upload
from src.core.auth import get_current_user
from src.core.cache_metrics import cache_refresh, cache_request
from src.database.models.user import User
from src.database.session import get_db
from src.helpers.async_storage import finish_storage_operation
from src.helpers.collection_artwork import render_cover, source_signature
from src.helpers.image_prefetch import WIDTHS
from src.helpers.media_collection_artwork import (
    cover_items,
    custom_cover_path,
    generated_cover_path,
    poster_path,
)
from src.helpers.remote_images import RemoteImageError
from src.helpers.remote_images import fetch_if_missing as fetch_and_store

router = APIRouter(
    prefix="/api/lists", tags=["media-lists"], dependencies=[Depends(get_current_user)]
)
_FAILED_POSTERS: OrderedDict[str, float] = OrderedDict()


async def _poster_source(user_id: UUID, item: dict) -> tuple[str, str, Path]:
    target = poster_path(user_id, item)
    key = str(target)
    if item["poster_url"] and not await asyncio.to_thread(target.is_file):
        if _FAILED_POSTERS.get(key, 0) <= time.monotonic():
            try:
                await asyncio.to_thread(
                    fetch_and_store, item["poster_url"], target, WIDTHS["poster"]
                )
                _FAILED_POSTERS.pop(key, None)
            except (RemoteImageError, OSError):
                _FAILED_POSTERS[key] = time.monotonic() + 30
                _FAILED_POSTERS.move_to_end(key)
                while len(_FAILED_POSTERS) > 256:
                    _FAILED_POSTERS.popitem(last=False)
    return f"{item['media_type']}:{item['media_id']}", item["title"], target


@router.get("/{list_id}/cover")
async def get_media_collection_cover(
    list_id: UUID,
    request: Request,
    *,
    owner: Annotated[UUID | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    lst = await _get_list_or_404(list_id, current_user.id, db)
    if owner is not None and owner != current_user.id:
        raise HTTPException(403, "This collection cover belongs to another account.")
    destination = custom_cover_path(current_user.id, list_id)
    if await asyncio.to_thread(destination.is_file):
        cache_request("collection_cover", "bypass")
    else:
        items = cover_items(lst, await _resolve_items(lst, current_user.id, db))
        # Fetch at most four absent posters concurrently through the existing
        # pinned-DNS, redirect/byte/pixel-limited downloader. Cached posters do no network work.
        rows = await asyncio.gather(*(_poster_source(current_user.id, item) for item in items))
        signature = await asyncio.to_thread(source_signature, rows)
        source_keys = ",".join(path.name for _, _, path in rows)
        key = hashlib.sha256(f"v1:{lst.name}:{signature}:{source_keys}".encode()).hexdigest()
        destination = generated_cover_path(current_user.id, list_id, key)
        if not await asyncio.to_thread(destination.is_file):
            cache_request("collection_cover", "miss")
            with cache_refresh("collection_cover"):
                await finish_storage_operation(
                    asyncio.to_thread(render_cover, rows, lst.name, destination)
                )
        else:
            cache_request("collection_cover", "hit")
    return await collection_cover_response(
        request,
        destination,
        "private, max-age=60, must-revalidate" if owner is not None else "private, no-cache",
    )


@router.put("/{list_id}/cover")
async def upload_media_collection_cover(
    list_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    await _get_list_or_404(list_id, current_user.id, db)
    return await store_cover_upload(file, custom_cover_path(current_user.id, list_id))


@router.delete("/{list_id}/cover", status_code=204)
async def remove_media_collection_cover(
    list_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    await _get_list_or_404(list_id, current_user.id, db)
    await finish_storage_operation(
        asyncio.to_thread(custom_cover_path(current_user.id, list_id).unlink, missing_ok=True)
    )
    return Response(status_code=204)
