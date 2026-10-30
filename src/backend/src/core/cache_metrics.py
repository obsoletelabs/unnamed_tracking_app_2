"""Bounded telemetry at existing cache boundaries; no cache policy or scrape-time I/O."""

import asyncio
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from src.core import metrics, request_metrics

logger = logging.getLogger(__name__)

_KINDS = frozenset(
    {
        "metadata_search",
        "metadata_provider",
        "artwork_preview",
        "collection_cover",
        "media_image",
        "notification_image",
    }
)
_LOOKUP_KINDS = frozenset({"metadata_search", "artwork_preview", "collection_cover", "media_image"})
_OUTCOMES = frozenset({"hit", "miss", "stale", "bypass", "negative_hit"})
_REASONS = frozenset({"expiry", "size_pressure", "invalidation"})

# Known zero event counts make a miss-only cache's hit ratio zero rather than
# absent. Idle ratios still remain undefined (0/0); no storage size is inferred.
for _kind in _LOOKUP_KINDS:
    for _outcome in _OUTCOMES:
        metrics.CACHE_REQUESTS.labels(_kind, _outcome)


def cache_request(kind: str, outcome: str) -> None:
    """Reject unknown labels rather than allowing user/provider values into a series."""
    if kind in _LOOKUP_KINDS and outcome in _OUTCOMES:
        metrics.CACHE_REQUESTS.labels(kind, outcome).inc()


def cache_eviction(kind: str, reason: str) -> None:
    """Count a removal only when an existing entry was actually removed."""
    if kind in _LOOKUP_KINDS and reason in _REASONS:
        metrics.CACHE_EVICTIONS.labels(kind, reason).inc()


def upstream_bytes(kind: str, amount: int) -> None:
    """Count bytes that were actually read, including a failed oversized response."""
    if kind in _KINDS and amount > 0:
        metrics.CACHE_UPSTREAM_BYTES.labels(kind).inc(amount)


def cache_entries(kind: str, count: int) -> None:
    """Publish an exact process-local entry count after a cache mutation."""
    if kind in _KINDS and count >= 0:
        metrics.CACHE_ENTRIES.labels(kind).set(count)
        metrics.CACHE_INVENTORY_UP.labels(kind).set(1)


def scan_disk_cache_inventory(root: Path, *, limit: int = 50_000) -> dict[str, tuple[int, int]]:
    """Bound a background scan of known derived-image directories, never a scrape."""
    totals = {"artwork_preview": [0, 0], "media_image": [0, 0]}
    deadline = time.monotonic() + 2
    for user in root.iterdir() if root.exists() else ():
        if time.monotonic() > deadline:
            raise ValueError("cache inventory time limit exceeded")
        if not user.is_dir():
            continue
        for kind, folder in (
            ("artwork_preview", "asset-previews"),
            ("media_image", "media-images"),
        ):
            cache_dir = user / ".cache" / folder
            for directory, _children, files in os.walk(cache_dir):
                if time.monotonic() > deadline:
                    raise ValueError("cache inventory time limit exceeded")
                for filename in files:
                    if not filename.endswith(".jpg"):
                        continue
                    totals[kind][0] += 1
                    if sum(values[0] for values in totals.values()) > limit:
                        raise ValueError("cache inventory entry limit exceeded")
                    try:
                        totals[kind][1] += (Path(directory) / filename).stat().st_size
                    except FileNotFoundError:
                        totals[kind][0] -= 1
    return {kind: (values[0], values[1]) for kind, values in totals.items()}


async def refresh_disk_cache_inventory(root: Path) -> None:
    """Run the bounded filesystem aggregation outside the scrape request path."""
    try:
        totals = await asyncio.to_thread(scan_disk_cache_inventory, root)
    except (OSError, ValueError, RuntimeError):
        logger.exception("Cache inventory refresh failed")
        for kind in ("artwork_preview", "media_image"):
            metrics.CACHE_INVENTORY_UP.labels(kind).set(0)
            metrics.CACHE_ENTRIES.remove(kind)
            metrics.CACHE_STORAGE_BYTES.remove(kind)
        return
    for kind, (entries, size) in totals.items():
        metrics.CACHE_ENTRIES.labels(kind).set(entries)
        metrics.CACHE_STORAGE_BYTES.labels(kind).set(size)
        metrics.CACHE_INVENTORY_UP.labels(kind).set(1)


@dataclass
class CacheRefresh:
    """Expected provider failures may be returned as data instead of exceptions."""

    outcome: str = "ok"

    def failed(self) -> None:
        self.outcome = "error"

    def cancelled(self) -> None:
        self.outcome = "cancelled"


@contextmanager
def cache_refresh(kind: str, *, active: bool = True) -> Iterator[CacheRefresh]:
    """Measure a refresh, without inventing upstream bytes or saved work."""
    result = CacheRefresh()
    started = time.perf_counter()
    try:
        yield result
    except asyncio.CancelledError:
        result.cancelled()
        raise
    except Exception:
        result.failed()
        raise
    finally:
        if active and kind in _LOOKUP_KINDS:
            outcome = result.outcome if result.outcome in {"ok", "error", "cancelled"} else "error"
            elapsed = time.perf_counter() - started
            metrics.CACHE_REFRESH_DURATION.labels(kind, outcome).observe(elapsed)
            request_metrics.observe_phase("cache." + kind, elapsed)
