"""Policy, SSRF-safe fetching and caching of images in rich notification content.

Producers can only name an image; the host decides whether viewers load it directly, load a copy
fetched and re-encoded by this server, or see built-in artwork. Fetching reuses the pinned-DNS,
redirect-checked, size-limited downloader, additionally caps bytes, bounds concurrency and
remembers failures briefly so a bad URL cannot be used to hammer a remote host. The cached copy is
a freshly re-encoded JPEG, so nothing a remote server sent is ever served as-is.
"""

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.anime import Anime
from src.database.models.movies import Movie
from src.database.models.tv_show import TVShow
from src.helpers.remote_images import RemoteImageError, download_image, save_shrunk
from src.plugin_api.notification_story_contracts import (
    ImageCapabilities,
    NotificationContent,
)

logger = logging.getLogger(__name__)

ImageMode = Literal["proxied", "external", "off"]
IMAGE_WIDTH = 960
MAX_CONCURRENT_FETCHES = 4
_HOST_MEDIA_MODELS: dict[str, Any] = {"movie": Movie, "tv": TVShow, "anime": Anime}
_fetch_slots: dict[int, asyncio.Semaphore] = {}

FALLBACK_ARTWORK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180" role="img" '
    'aria-label="Image unavailable"><rect width="320" height="180" fill="#2b3138"/>'
    '<path d="M110 120l30-38 22 26 16-18 32 30z" fill="#56606b"/>'
    '<circle cx="124" cy="66" r="12" fill="#56606b"/></svg>'
)


def effective_image_mode(capabilities: ImageCapabilities | None = None) -> ImageMode:
    """The configured host policy, narrowed by what the viewing surface can actually show."""
    configured: ImageMode = settings.NOTIFICATION_IMAGE_MODE
    if capabilities is None:
        return configured
    if configured == "proxied" and not capabilities.proxied_urls:
        return "external" if capabilities.external_urls else "off"
    if configured == "external" and not capabilities.external_urls:
        return "proxied" if capabilities.proxied_urls else "off"
    return configured


def _slots() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _fetch_slots:
        _fetch_slots.clear()
        _fetch_slots[loop_id] = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
    return _fetch_slots[loop_id]


def cache_file(url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()
    return Path(settings.NOTIFICATION_IMAGE_CACHE_DIR) / digest[:2] / f"{digest}.jpg"


def _failure_marker(url: str) -> Path:
    return cache_file(url).with_suffix(".fail")


def _recent_failure(url: str) -> bool:
    marker = _failure_marker(url)
    try:
        return time.time() - marker.stat().st_mtime < settings.NOTIFICATION_IMAGE_FAILURE_SECONDS
    except OSError:
        return False


def _store(url: str) -> None:
    target = cache_file(url)
    try:
        data = download_image(
            url,
            max_bytes=settings.NOTIFICATION_IMAGE_MAX_BYTES,
            cache_kind="notification_image",
        )
        save_shrunk(data, target, IMAGE_WIDTH)
        _failure_marker(url).unlink(missing_ok=True)
    except RemoteImageError:
        _failure_marker(url).parent.mkdir(parents=True, exist_ok=True)
        _failure_marker(url).touch()
        raise


async def cached_image(url: str) -> Path | None:
    """The local copy of a validated external image, fetching it once; None selects fallback art."""
    target = cache_file(url)
    if target.is_file():
        return target
    if _recent_failure(url):
        return None
    async with _slots():
        if target.is_file():
            return target
        try:
            await asyncio.to_thread(_store, url)
        except (RemoteImageError, OSError, ValueError) as exc:
            logger.info("Notification image could not be cached: %s", type(exc).__name__)
            return None
    return target if target.is_file() else None


async def host_media_path(
    db: AsyncSession, user_id: UUID, media_type: str, media_id: UUID
) -> str | None:
    """Existing protected media route, only for a record the recipient owns."""
    model = _HOST_MEDIA_MODELS.get(media_type)
    if model is None:
        return None
    owned = await db.scalar(select(model.id).where(model.id == media_id, model.user_id == user_id))
    return f"/api/media-image/{media_type}/{media_id}/poster" if owned else None


async def resolve_images(
    db: AsyncSession,
    content: NotificationContent,
    *,
    user_id: UUID,
    proxy_path: str,
    mode: ImageMode,
) -> list[dict[str, Any]]:
    """Per-image display entry: `state` is proxied, external, host_media or fallback."""
    resolved: list[dict[str, Any]] = []
    for index, image in enumerate(content.images):
        entry: dict[str, Any] = {
            "index": index,
            "role": image.role,
            "alt": image.alt,
            "state": "fallback",
            "url": None,
        }
        source = image.source
        if source.kind == "host_media":
            path = await host_media_path(db, user_id, source.media_type, source.media_id)
            if path:
                entry |= {"state": "host_media", "url": path}
        elif mode == "external":
            entry |= {"state": "external", "url": source.url}
        elif mode == "proxied":
            entry |= {"state": "proxied", "url": f"{proxy_path}/{index}"}
        resolved.append(entry)
    return resolved
