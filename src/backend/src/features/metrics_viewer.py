"""Bounded, read-only snapshots of existing telemetry for the administrator viewer."""

import asyncio
import math
import time
from typing import Any

from src.core import metrics

_HISTOGRAMS = {
    "uta_http_request_duration_seconds": "http",
    "uta_http_phase_duration_seconds": "phases",
    "uta_database_query_group_duration_seconds": "sql",
    "uta_database_pool_wait_seconds": "pool_wait",
    "uta_database_commit_duration_seconds": "commits",
    "uta_plugin_runtime_request_duration_seconds": "runtime",
    "uta_cache_refresh_duration_seconds": "cache",
}
_VALUES = {
    "uta_http_requests_total": "requests",
    "uta_http_database_queries_total": "sql_queries",
    "uta_http_database_query_seconds_total": "sql_seconds",
    "uta_database_named_pool_connections": "pools",
    "uta_database_named_pool_timeouts_total": "pool_timeouts",
    "uta_cache_access_total": "cache_access",
    "process_cpu_seconds_total": "cpu_seconds",
    "process_resident_memory_bytes": "memory_bytes",
    "uta_metrics_payload_bytes": "scrape_bytes",
}
_SAMPLE_NAMES = frozenset(_VALUES) | frozenset(
    name + suffix for name in _HISTOGRAMS for suffix in ("_bucket", "_count", "_sum")
)
_ALLOWED_LABELS = frozenset({
    "method", "route", "phase", "pool", "query_group", "operation", "outcome",
    "state", "status_class", "cache_kind", "result",
})
_STARTED = time.time()
_TTL = 5.0
_value: dict[str, Any] | None = None
_refreshed: float = 0.0
_pending: asyncio.Task[dict[str, Any]] | None = None


def _collect() -> dict[str, Any]:
    histograms: dict[str, dict[tuple, dict[str, Any]]] = {key: {} for key in _HISTOGRAMS.values()}
    values: dict[str, list[dict[str, Any]]] = {key: [] for key in _VALUES.values()}
    for family in metrics.collect_families(_SAMPLE_NAMES):
        histogram = _HISTOGRAMS.get(family.name)
        for sample in family.samples:
            labels = {key: value for key, value in sample.labels.items() if key in _ALLOWED_LABELS}
            if histogram and sample.name != family.name + "_created":
                rows = histograms[histogram]
                key = tuple(sorted(labels.items()))
                row = rows.setdefault(key, {"labels": labels, "count": 0, "sum": 0, "buckets": []})
                if sample.name.endswith("_bucket"):
                    row["buckets"].append([sample.labels["le"], sample.value])
                elif sample.name.endswith("_count"):
                    row["count"] = sample.value
                elif sample.name.endswith("_sum"):
                    row["sum"] = sample.value
            elif sample.name in _VALUES and math.isfinite(sample.value):
                values[_VALUES[sample.name]].append({"labels": labels, "value": sample.value})
    return {
        "enabled": True,
        "collected_at": time.time(),
        "started_at": None if metrics.multiprocess_enabled() else _STARTED,
        "scope": "workers" if metrics.multiprocess_enabled() else "process",
        "histograms": {key: list(rows.values()) for key, rows in histograms.items()},
        "values": values,
    }


async def snapshot() -> dict[str, Any]:
    """Share a short cache and off-loop collection; never scrape SQL, plugins or providers."""
    global _value, _refreshed, _pending  # pylint: disable=global-statement
    if not metrics.metrics_enabled():
        return {"enabled": False}
    if _value is not None and time.monotonic() - _refreshed < _TTL:
        return _value
    if _pending is None or _pending.done():
        _pending = asyncio.create_task(asyncio.to_thread(_collect))
    result = await asyncio.shield(_pending)
    _value, _refreshed = result, time.monotonic()
    return result
