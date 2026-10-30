"""Bounded request-local timings at existing boundaries, with no I/O or background work."""

import time
from _thread import LockType
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from src.core import metrics

_PHASES = frozenset(
    {
        "auth.reserve",
        "auth.verify",
        "auth.refund",
        "storage.prepare",
        "plugin.runtime",
        "pwa.provider",
        "pwa.assets",
        "database.application",
        "database.authentication",
        "pool.application",
        "pool.authentication",
        "commit.application",
        "commit.authentication",
        "cache.artwork_preview",
        "cache.collection_cover",
        "cache.metadata_search",
        "cache.media_image",
    }
)
_POOLS = frozenset({"application", "authentication"})
_QUERY_GROUPS = frozenset(
    {
        "credentials",
        "auth_quota",
        "games",
        "media",
        "plugins",
        "notifications",
        "settings",
        "control",
        "other",
    }
)
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


@dataclass
class RequestMeasurements:
    """Stop accepting work at headers so streaming/background work is not TTFB."""

    phases: dict[str, float] = field(default_factory=dict)
    queries: dict[tuple[str, str], tuple[int, float]] = field(default_factory=dict)
    active: bool = True
    lock: LockType = field(default_factory=Lock, repr=False)

    def server_timing(self) -> str:
        """Only fixed names and numeric durations; no route, actor or query values."""
        with self.lock:
            values = [
                f"{name.replace('.', '_')};dur={seconds * 1000:.3f}"
                for name, seconds in self.phases.items()
            ]
            if self.queries:
                count = sum(item[0] for item in self.queries.values())
                values.append(f'sql_queries;desc="{count}"')
        return ", ".join(values)

    def emit(self, scope: Mapping[str, Any]) -> None:
        with self.lock:
            if not self.active:
                return
            self.active = False
            phases, queries = self.phases.copy(), self.queries.copy()
        method = scope["method"] if scope["method"] in _METHODS else "OTHER"
        route = metrics.route_label(scope)
        for phase_name, seconds in phases.items():
            metrics.HTTP_PHASE_DURATION.labels(method, route, phase_name).observe(seconds)
        for (pool, group), (count, seconds) in queries.items():
            metrics.HTTP_DATABASE_QUERIES.labels(method, route, pool, group).inc(count)
            metrics.HTTP_DATABASE_SECONDS.labels(method, route, pool, group).inc(seconds)


_current: ContextVar[RequestMeasurements | None] = ContextVar(
    "uta_request_measurements", default=None
)


def begin() -> tuple[RequestMeasurements | None, Token[RequestMeasurements | None]]:
    measurement = RequestMeasurements() if metrics.metrics_enabled() else None
    return measurement, _current.set(measurement)


def reset(token: Token[RequestMeasurements | None]) -> None:
    _current.reset(token)


def observe_phase(phase_name: str, seconds: float) -> None:
    measurement = _current.get()
    if measurement is None or phase_name not in _PHASES or seconds < 0:
        return
    with measurement.lock:
        if measurement.active:
            measurement.phases[phase_name] = measurement.phases.get(phase_name, 0) + seconds


def observe_query(pool: str, group: str, seconds: float) -> None:
    measurement = _current.get()
    if measurement is None or pool not in _POOLS or group not in _QUERY_GROUPS or seconds < 0:
        return
    with measurement.lock:
        if measurement.active:
            count, elapsed = measurement.queries.get((pool, group), (0, 0))
            measurement.queries[pool, group] = (count + 1, elapsed + seconds)
            phase_name = "database." + pool
            measurement.phases[phase_name] = measurement.phases.get(phase_name, 0) + seconds


@contextmanager
def phase(name: str) -> Iterator[None]:
    measurement = _current.get()
    if measurement is None or not measurement.active or name not in _PHASES:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        observe_phase(name, time.perf_counter() - started)
