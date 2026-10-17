"""Shared HTTP fallback for images that could not be cached locally."""

import asyncio
from pathlib import Path

from fastapi import status
from fastapi.responses import FileResponse, RedirectResponse, Response

from src.helpers.image_prefetch import WIDTHS
from src.helpers.remote_images import RemoteImageError, fetch_and_store, icon_cache_path


def original_image_response(url: str) -> RedirectResponse:
    """Let the browser load the original image without caching the fallback."""
    return RedirectResponse(
        url,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Cache-Control": "no-store"},
    )


async def cached_icon_response(root: Path, url: str) -> Response:
    """Serve an icon from the local cache, downloading it once if it is missing."""
    target = icon_cache_path(root, url)
    response = FileResponse(
        target, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"}
    )
    if not target.is_file():
        try:
            await asyncio.to_thread(fetch_and_store, url, target, WIDTHS["icon"])
        except RemoteImageError:
            return original_image_response(url)
    return response
