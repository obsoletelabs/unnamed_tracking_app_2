"""Bounded telemetry at existing cache boundaries; no cache policy or scrape-time I/O."""

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from src.core import metrics

_KINDS = frozenset({"metadata_search", "artwork_preview", "collection_cover"})
_OUTCOMES = frozenset({"hit", "miss", "stale", "bypass", "negative_hit"})
_REASONS = frozenset({"expiry", "size_pressure", "invalidation"})

# Known zero event counts make a miss-only cache's hit ratio zero rather than
# absent. Idle ratios still remain undefined (0/0); no storage size is inferred.
for _kind in _KINDS:
    for _outcome in _OUTCOMES:
        metrics.CACHE_REQUESTS.labels(_kind, _outcome)


def cache_request(kind: str, outcome: str) -> None:
    """Reject unknown labels rather than allowing user/provider values into a series."""
    if kind in _KINDS and outcome in _OUTCOMES:
        metrics.CACHE_REQUESTS.labels(kind, outcome).inc()


def cache_eviction(kind: str, reason: str) -> None:
    """Count a removal only when an existing entry was actually removed."""
    if kind in _KINDS and reason in _REASONS:
        metrics.CACHE_EVICTIONS.labels(kind, reason).inc()


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
        if active and kind in _KINDS:
            outcome = result.outcome if result.outcome in {"ok", "error", "cancelled"} else "error"
            metrics.CACHE_REFRESH_DURATION.labels(kind, outcome).observe(
                time.perf_counter() - started
            )
