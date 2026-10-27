"""Account-partitioned cover URLs and storage for persisted media collections."""

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from src.core.cache_metrics import cache_eviction
from src.helpers.image_prefetch import MEDIA_ROOT
from src.helpers.remote_images import cache_path

_DATA_ROOT = MEDIA_ROOT


def custom_cover_path(user_id: UUID, list_id: UUID) -> Path:
    return _DATA_ROOT / str(user_id) / "media-list-covers" / f"{list_id}.webp"


def cover_items(lst: Any, items: list[dict]) -> list[dict]:
    ordered = list(items)
    if lst.cover_media_id is not None:
        ordered.sort(key=lambda item: 0 if item["media_id"] == lst.cover_media_id else 1)
    return ordered[:4]


def poster_path(user_id: UUID, item: dict) -> Path:
    return cache_path(
        _DATA_ROOT / str(user_id) / ".cache",
        item["media_type"],
        str(item["media_id"]),
        "poster",
        item["poster_url"] or "",
    )


def cover_metadata(lst: Any, items: list[dict]) -> dict[str, Any]:
    """Called in the existing summary's worker; never blocks the event loop on stat."""
    try:
        info = custom_cover_path(lst.user_id, lst.id).stat()
        custom = (info.st_mtime_ns, info.st_size)
    except FileNotFoundError:
        custom = None
    revision = hashlib.sha256(
        json.dumps(
            [
                lst.name,
                custom,
                [
                    (str(item["media_id"]), item["media_type"], item["poster_url"])
                    for item in items[:4]
                ],
            ]
        ).encode()
    ).hexdigest()[:24]
    return {
        "cover_url": f"/api/lists/{lst.id}/cover?{urlencode({'owner': str(lst.user_id), 'v': revision})}",
        "has_custom_cover": custom is not None,
    }


def generated_cover_path(user_id: UUID, list_id: UUID, signature: str) -> Path:
    return (
        _DATA_ROOT
        / str(user_id)
        / ".cache"
        / "media-list-covers"
        / str(list_id)
        / f"{signature}.webp"
    )


def remove_collection_files(user_id: UUID, list_id: UUID) -> None:
    custom_cover_path(user_id, list_id).unlink(missing_ok=True)
    folder = generated_cover_path(user_id, list_id, "unused").parent
    for path in folder.glob("*.webp"):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        cache_eviction("collection_cover", "invalidation")
