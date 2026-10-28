"""Prometheus metric definitions for the host application.

Every label here comes from a fixed or template-derived set: route templates, HTTP methods,
status classes, authentication operations, job ids from the host registry, core provider ids
and plugin runtime operations. Nothing a user submitted (URLs with identifiers, titles,
usernames, email addresses, tokens, addresses) is ever used as a label value.

The endpoint itself (`/metrics`) is served by `src.api.metrics`; this module only owns the
registry, the instruments and the small amount of work done at scrape time.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
    multiprocess,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

from src.core.config import settings

logger = logging.getLogger(__name__)

CONTENT_TYPE = CONTENT_TYPE_LATEST
MINIMUM_TOKEN_LENGTH = 32
# Dependency snapshots are cached so that a short scrape interval cannot add load.
SNAPSHOT_SECONDS = 15.0
PLUGIN_SNAPSHOT_SECONDS = 60.0
SNAPSHOT_TIMEOUT_SECONDS = 2.0

_MULTIPROCESS_DIRECTORY = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip()

REGISTRY = CollectorRegistry(auto_describe=True)

_REQUEST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
_SHORT_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
_JOB_BUCKETS = (0.1, 0.5, 1, 5, 15, 60, 300, 900, 3600)

# `route` is the route template ("/api/games/{game_id}"), or "unmatched" for no route.
HTTP_REQUESTS = Counter(
    "uta_http_requests_total",
    "HTTP requests by route template, method and status class.",
    ("method", "route", "status_class"),
    registry=REGISTRY,
)
HTTP_DURATION = Histogram(
    "uta_http_request_duration_seconds",
    "Time to produce the response start, by route template and method.",
    ("method", "route"),
    buckets=_REQUEST_BUCKETS,
    registry=REGISTRY,
)
HTTP_RESPONSE_DURATION = Histogram(
    "uta_http_response_duration_seconds",
    "Time until the final ASGI body send or terminal error/disconnect, excluding background work.",
    ("method", "route", "response_kind", "outcome"),
    buckets=_REQUEST_BUCKETS,
    registry=REGISTRY,
)
HTTP_RESPONSE_BYTES = Counter(
    "uta_http_response_bytes_total",
    "Application body bytes accepted by ASGI send, before reverse-proxy compression.",
    ("method", "route", "response_kind"),
    registry=REGISTRY,
)
HTTP_REQUEST_BYTES = Counter(
    "uta_http_request_bytes_total",
    "Request body bytes consumed by the application, without rereading or buffering.",
    ("method", "route"),
    registry=REGISTRY,
)
HTTP_DISCONNECTS = Counter(
    "uta_http_disconnects_total",
    "Observed disconnects, send failures or task cancellations, once per request.",
    ("method", "route", "phase"),
    registry=REGISTRY,
)
CACHE_REQUESTS = Counter(
    "uta_cache_requests_total",
    "Cache lookups by fixed kind and hit/miss/stale/bypass/negative_hit outcome.",
    ("cache_kind", "outcome"),
    registry=REGISTRY,
)
CACHE_REFRESH_DURATION = Histogram(
    "uta_cache_refresh_duration_seconds",
    "Metadata fetch/store or derived-artwork rebuild duration, including failures.",
    ("cache_kind", "outcome"),
    buckets=_SHORT_BUCKETS,
    registry=REGISTRY,
)
CACHE_EVICTIONS = Counter(
    "uta_cache_evictions_total",
    "Observed cache entry removals by fixed kind and reason; not a storage inventory.",
    ("cache_kind", "reason"),
    registry=REGISTRY,
)
CACHE_ENTRIES = Gauge(
    "uta_cache_entries",
    "Measured cache entries by fixed cache kind; absent when inventory is unavailable.",
    ("cache_kind",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
CACHE_STORAGE_BYTES = Gauge(
    "uta_cache_storage_bytes",
    "Measured on-disk cache bytes; budgets are not exported when none is configured.",
    ("cache_kind",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
CACHE_INVENTORY_UP = Gauge(
    "uta_cache_inventory_up",
    "1 when the last bounded cache inventory completed, 0 when unavailable.",
    ("cache_kind",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
CACHE_UPSTREAM_BYTES = Counter(
    "uta_cache_upstream_bytes_total",
    "Actual bytes received from upstream metadata and image services.",
    ("cache_kind",),
    registry=REGISTRY,
)
DATABASE_QUERY_DURATION = Histogram(
    "uta_database_query_duration_seconds",
    "SQL cursor execution time, excluding pool wait and result consumption.",
    ("operation", "outcome"),
    buckets=_REQUEST_BUCKETS,
    registry=REGISTRY,
)
DATABASE_CONNECTION_WAIT = Histogram(
    "uta_database_connection_wait_seconds",
    "Time to acquire or create a connection from the application pool.",
    buckets=_REQUEST_BUCKETS,
    registry=REGISTRY,
)
DATABASE_POOL_TIMEOUTS = Counter(
    "uta_database_pool_timeouts_total",
    "Application pool acquisition timeouts.",
    ("reason",),
    registry=REGISTRY,
)
DATABASE_TRANSACTIONS = Counter(
    "uta_database_transactions_total",
    "Completed application session transaction transitions.",
    ("outcome",),
    registry=REGISTRY,
)
HTTP_IN_FLIGHT = Gauge(
    "uta_http_requests_in_flight",
    "HTTP requests currently being handled.",
    multiprocess_mode="livesum",
    registry=REGISTRY,
)
HTTP_EXCEPTIONS = Counter(
    "uta_http_exceptions_total",
    "Requests that ended in an unhandled exception, by route template.",
    ("route",),
    registry=REGISTRY,
)
HTTP_RATE_LIMITED = Counter(
    "uta_http_rate_limited_responses_total",
    "Responses with status 429, by route template.",
    ("route",),
    registry=REGISTRY,
)

AUTH_DECISIONS = Counter(
    "uta_auth_rate_limit_decisions_total",
    "Authentication quota decisions by operation and outcome (aggregate, never per account).",
    ("operation", "outcome"),
    registry=REGISTRY,
)
AUTH_ATTEMPTS = Counter(
    "uta_auth_attempts_total",
    "Terminal sign-in outcomes by fixed authentication flow and result.",
    ("flow", "outcome"),
    registry=REGISTRY,
)
OIDC_STAGE_DURATION = Histogram(
    "uta_oidc_stage_duration_seconds",
    "OIDC discovery, token/claims and account-link stage duration.",
    ("stage", "outcome"),
    buckets=_SHORT_BUCKETS,
    registry=REGISTRY,
)
OIDC_FAILURES = Counter(
    "uta_oidc_failures_total",
    "OIDC failures grouped by a host-defined safe reason.",
    ("reason",),
    registry=REGISTRY,
)
AUTH_SESSION_TRANSITIONS = Counter(
    "uta_auth_session_transitions_total",
    "Observed browser session creation, revocation and expiry cleanup.",
    ("transition",),
    registry=REGISTRY,
)
AUTH_SESSIONS = Gauge(
    "uta_auth_sessions",
    "Browser sessions by state, refreshed at most every 15 seconds.",
    ("state",),
    multiprocess_mode="max",
    registry=REGISTRY,
)

DATABASE_UP = Gauge(
    "uta_database_up",
    "1 when the last cached database probe succeeded.",
    multiprocess_mode="max",
    registry=REGISTRY,
)
DATABASE_PROBE_SECONDS = Gauge(
    "uta_database_probe_duration_seconds",
    "Duration of the last cached database probe.",
    multiprocess_mode="max",
    registry=REGISTRY,
)

JOB_RUNS = Counter(
    "uta_job_runs_total",
    "Scheduled and manual job steps by job and outcome.",
    ("job", "outcome"),
    registry=REGISTRY,
)
JOB_DURATION = Histogram(
    "uta_job_run_duration_seconds",
    "Duration of one job step.",
    ("job",),
    buckets=_JOB_BUCKETS,
    registry=REGISTRY,
)
JOB_LAST_SUCCESS = Gauge(
    "uta_job_last_success_timestamp_seconds",
    "Unix time of a job's last successful run, from the persisted job state.",
    ("job",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
JOB_PENDING = Gauge(
    "uta_job_pending",
    "Due scheduled work observed on the last scheduler tick, by fixed class.",
    ("task_class",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
JOB_SCHEDULE_DELAY = Histogram(
    "uta_job_schedule_delay_seconds",
    "Delay from the persisted due time to a scheduled start.",
    ("task_class",),
    buckets=_JOB_BUCKETS,
    registry=REGISTRY,
)
JOB_SCHEDULE_OUTCOMES = Counter(
    "uta_job_schedule_outcomes_total",
    "Scheduled starts and observed skips by fixed task class.",
    ("task_class", "outcome"),
    registry=REGISTRY,
)
BACKUP_OPERATIONS = Counter(
    "uta_backup_operations_total",
    "Backup create and JSON readback outcomes.",
    ("backend", "operation", "outcome"),
    registry=REGISTRY,
)
BACKUP_DURATION = Histogram(
    "uta_backup_operation_duration_seconds",
    "Backup write and JSON readback duration.",
    ("backend", "operation"),
    buckets=_JOB_BUCKETS,
    registry=REGISTRY,
)
BACKUP_LAST_VERIFIED = Gauge(
    "uta_backup_last_verified_timestamp_seconds",
    "Last completed JSON readback verification.",
    ("backend",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
BACKUP_ARCHIVE_BYTES = Gauge(
    "uta_backup_archive_bytes",
    "Size of the latest JSON snapshot checked by readback.",
    ("backend",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
MIGRATION_RECOVERY_STATUS = Gauge(
    "uta_migration_recovery_status",
    "Latest persisted pre-migration attempt status observed at startup.",
    ("status",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
MIGRATION_MAINTENANCE_DURATION = Gauge(
    "uta_migration_maintenance_duration_seconds",
    "Elapsed wall time of the latest completed persisted migration attempt.",
    multiprocess_mode="max",
    registry=REGISTRY,
)
DATA_OPERATIONS = Counter(
    "uta_data_operations_total",
    "Completed host import, export and sync operations.",
    ("integration", "operation", "outcome"),
    registry=REGISTRY,
)
DATA_OPERATION_DURATION = Histogram(
    "uta_data_operation_duration_seconds",
    "Duration of host import, export and sync operations.",
    ("integration", "operation"),
    buckets=_JOB_BUCKETS,
    registry=REGISTRY,
)
DATA_RECORDS = Counter(
    "uta_data_records_total",
    "Committed record outcomes from host-owned integrations.",
    ("integration", "entity", "outcome"),
    registry=REGISTRY,
)
DATA_TRANSFER_BYTES = Counter(
    "uta_data_transfer_bytes_total",
    "Observed serialized export and uploaded file bytes.",
    ("integration", "direction"),
    registry=REGISTRY,
)
INTEGRATION_LAST_SUCCESS = Gauge(
    "uta_integration_last_success_timestamp_seconds",
    "Last successful host integration operation.",
    ("integration",),
    multiprocess_mode="max",
    registry=REGISTRY,
)

PLUGIN_REPORTED_EVENTS = Counter(
    "uta_plugin_reported_events_total",
    "Explicit plugin-reported events from the fixed host vocabulary; no plugin-supplied labels.",
    ("event",),
    registry=REGISTRY,
)

PROVIDER_CALLS = Counter(
    "uta_metadata_provider_calls_total",
    "Core metadata provider operations by provider, operation and outcome.",
    ("provider", "operation", "outcome"),
    registry=REGISTRY,
)
PROVIDER_DURATION = Histogram(
    "uta_metadata_provider_call_duration_seconds",
    "Core metadata provider operation latency.",
    ("provider", "operation"),
    buckets=_SHORT_BUCKETS,
    registry=REGISTRY,
)

PLUGIN_RUNTIME_UP = Gauge(
    "uta_plugin_runtime_up",
    "1 when the plugin runtime answered its last cached health probe.",
    multiprocess_mode="max",
    registry=REGISTRY,
)
PLUGINS = Gauge(
    "uta_plugins",
    "Installed plugins by state, from the runtime's cached plugin list.",
    ("state",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
PLUGIN_RUNTIME_REQUESTS = Counter(
    "uta_plugin_runtime_requests_total",
    "Requests from the host to the plugin runtime by operation and outcome.",
    ("operation", "outcome"),
    registry=REGISTRY,
)
PLUGIN_RUNTIME_DURATION = Histogram(
    "uta_plugin_runtime_request_duration_seconds",
    "Latency of requests from the host to the plugin runtime by operation.",
    ("operation",),
    buckets=_SHORT_BUCKETS,
    registry=REGISTRY,
)
PLUGIN_GATEWAY_DECISIONS = Counter(
    "uta_plugin_gateway_decisions_total",
    "Host gateway permission decisions by fixed capability, principal boundary and reason.",
    ("capability", "boundary", "decision", "reason"),
    registry=REGISTRY,
)
PLUGIN_LIFECYCLE_OPERATIONS = Counter(
    "uta_plugin_lifecycle_operations_total",
    "Host-owned plugin lifecycle outcomes.",
    ("operation", "outcome"),
    registry=REGISTRY,
)
PLUGIN_PACKAGE_VALIDATION_FAILURES = Counter(
    "uta_plugin_package_validation_failures_total",
    "Rejected plugin package inspections by bounded failure class.",
    ("failure_class",),
    registry=REGISTRY,
)
PLUGIN_LIFECYCLE_QUEUE_WAIT = Histogram(
    "uta_plugin_lifecycle_queue_wait_seconds",
    "Wait for the host package transaction lock before install or recovery.",
    buckets=_SHORT_BUCKETS,
    registry=REGISTRY,
)
NOTIFICATION_DELIVERIES = Counter(
    "uta_notification_deliveries_total",
    "Logical delivery transitions and retry scheduling by fixed channel and reason.",
    ("channel", "outcome", "reason"),
    registry=REGISTRY,
)
NOTIFICATION_ATTEMPTS = Counter(
    "uta_notification_attempts_total",
    "Physical notification provider calls by fixed channel and outcome.",
    ("channel", "outcome"),
    registry=REGISTRY,
)
NOTIFICATION_DELIVERY_DURATION = Histogram(
    "uta_notification_delivery_duration_seconds",
    "Wall time of one physical provider delivery or edit attempt.",
    ("channel", "outcome"),
    buckets=_SHORT_BUCKETS,
    registry=REGISTRY,
)
NOTIFICATION_PENDING = Gauge(
    "uta_notification_pending",
    "Cached deployment-wide delivery rows by channel and waiting state.",
    ("channel", "state"),
    multiprocess_mode="max",
    registry=REGISTRY,
)
NOTIFICATION_OLDEST_PENDING = Gauge(
    "uta_notification_oldest_pending_timestamp_seconds",
    "Earliest next-attempt time among queued delivery rows; zero for an empty queue.",
    ("channel",),
    multiprocess_mode="max",
    registry=REGISTRY,
)
NOTIFICATION_BACKLOG_UP = Gauge(
    "uta_notification_backlog_up",
    "1 when the last bounded notification backlog aggregation completed.",
    multiprocess_mode="max",
    registry=REGISTRY,
)

BUILD_INFO = Info("uta_build", "Application build information.", registry=REGISTRY)
BUILD_INFO.info({"version": os.environ.get("APP_VERSION", "").strip()[:64] or "unknown"})
READY = Gauge(
    "uta_ready",
    "1 once the database probe and start-up have succeeded.",
    multiprocess_mode="max",
    registry=REGISTRY,
)


class _DatabasePoolCollector:  # pylint: disable=too-few-public-methods
    """Connection pool use, read from the pool object without touching the database."""

    def collect(self) -> Iterable[GaugeMetricFamily]:
        # Imported lazily: the session module builds the engine at import time.
        # pylint: disable-next=import-outside-toplevel
        from src.database.session import engine

        pool = engine.pool
        values = {
            "size": getattr(pool, "size", lambda: 0)(),
            "checked_out": getattr(pool, "checkedout", lambda: 0)(),
            "overflow": max(0, getattr(pool, "overflow", lambda: 0)()),
        }
        family = GaugeMetricFamily(
            "uta_database_pool_connections",
            "Connections in the application's SQLAlchemy pool by state.",
            labels=("state",),
        )
        for state, value in values.items():
            family.add_metric([state], value)
        yield family


def _build_registry() -> CollectorRegistry:
    """The registry rendered at scrape time (aggregated across workers when configured)."""
    if _MULTIPROCESS_DIRECTORY:
        aggregate = CollectorRegistry()
        multiprocess.MultiProcessCollector(aggregate, path=_MULTIPROCESS_DIRECTORY)
        return aggregate
    return REGISTRY


if not _MULTIPROCESS_DIRECTORY:
    # Standard process exporters; in multi-worker mode each worker would report the same
    # host, so operators scrape node/process exporters for those instead.
    ProcessCollector(registry=REGISTRY)
    PlatformCollector(registry=REGISTRY)
    GCCollector(registry=REGISTRY)
    REGISTRY.register(_DatabasePoolCollector())  # type: ignore[arg-type]


def metrics_enabled() -> bool:
    return bool(settings.METRICS_ENABLED)


def token_valid(presented: str | None) -> bool:
    """Constant-time bearer comparison; a missing or too-short configured token never matches."""
    expected = settings.METRICS_TOKEN
    if not presented or not expected or len(expected) < MINIMUM_TOKEN_LENGTH:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


class _Snapshot:  # pylint: disable=too-few-public-methods
    """One cached, timeout-bounded dependency probe."""

    def __init__(self, ttl: float, probe: Callable[[], Awaitable[None]]) -> None:
        self.ttl = ttl
        self.probe = probe
        self.refreshed_at = float("-inf")
        self.lock = asyncio.Lock()

    async def refresh(self) -> None:
        if time.monotonic() - self.refreshed_at < self.ttl:
            return
        async with self.lock:
            if time.monotonic() - self.refreshed_at < self.ttl:
                return
            try:
                await asyncio.wait_for(self.probe(), SNAPSHOT_TIMEOUT_SECONDS)
            # A failing dependency is reported as a metric and must not fail the scrape.
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("A metrics dependency probe failed", exc_info=False)
            self.refreshed_at = time.monotonic()


async def _probe_database() -> None:
    # Imported lazily: these modules import the engine and the job registry.
    # pylint: disable=import-outside-toplevel,not-callable
    from sqlalchemy import func, select, text

    from src.database.models.auth import UserSession
    from src.database.models.job_setting import JobSetting
    from src.database.session import SessionLocal
    from src.features.jobs import JOBS

    started = time.monotonic()
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
            now = int(time.time())
            active = await db.scalar(
                select(func.count())
                .select_from(UserSession)
                .where(UserSession.revoked_at.is_(None), UserSession.expires_at > now)
            )
            revoked = await db.scalar(
                select(func.count())
                .select_from(UserSession)
                .where(UserSession.revoked_at.is_not(None), UserSession.expires_at > now)
            )
            rows = (
                await db.execute(
                    select(JobSetting.job_id, JobSetting.last_success_at).where(
                        JobSetting.job_id.in_(tuple(JOBS)), JobSetting.last_success_at.is_not(None)
                    )
                )
            ).all()
    except Exception:
        DATABASE_UP.set(0)
        READY.set(0)
        raise
    finally:
        DATABASE_PROBE_SECONDS.set(time.monotonic() - started)
    DATABASE_UP.set(1)
    READY.set(1)
    AUTH_SESSIONS.labels("active").set(active or 0)
    AUTH_SESSIONS.labels("revoked").set(revoked or 0)
    for job_id, last_success_at in rows:
        JOB_LAST_SUCCESS.labels(job_id).set(last_success_at)


async def _probe_runtime_health() -> None:
    # pylint: disable-next=import-outside-toplevel
    from src.plugin_api.runtime_client import PluginRuntimeClient

    try:
        await PluginRuntimeClient().health()
    except Exception:
        PLUGIN_RUNTIME_UP.set(0)
        raise
    PLUGIN_RUNTIME_UP.set(1)


async def _probe_plugins() -> None:
    # pylint: disable-next=import-outside-toplevel
    from src.plugin_api.runtime_client import PluginRuntimeClient

    plugins = await PluginRuntimeClient().plugins()
    counts = {"installed": 0, "enabled": 0, "healthy": 0, "unhealthy": 0}
    for plugin in plugins:
        counts["installed"] += 1
        counts["enabled"] += 1 if plugin.get("enabled") is True else 0
        if plugin.get("health") == "healthy":
            counts["healthy"] += 1
        elif plugin.get("health") == "unhealthy":
            counts["unhealthy"] += 1
    for state, count in counts.items():
        PLUGINS.labels(state).set(count)


_snapshots = (
    _Snapshot(SNAPSHOT_SECONDS, _probe_database),
    _Snapshot(SNAPSHOT_SECONDS, _probe_runtime_health),
    _Snapshot(PLUGIN_SNAPSHOT_SECONDS, _probe_plugins),
)


async def render() -> bytes:
    """Refresh the cached dependency state, then serialise every metric."""
    await asyncio.gather(*(snapshot.refresh() for snapshot in _snapshots))
    return generate_latest(_build_registry())


def route_label(scope: Mapping[str, Any]) -> str:
    """The matched route template; never the raw path, which can hold identifiers."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "unmatched"


def plugin_runtime_operation(method: str, path: str) -> str:
    """Collapse a runtime request path to a bounded operation name.

    `/plugins/<id>/start` becomes `POST /plugins/{id}/start`; the plugin identifier is never
    a label value, and any shape this does not recognise is `other`."""
    parts = [part for part in path.split("?", 1)[0].split("/") if part]
    if not parts:
        return f"{method} /"
    if parts[0] != "plugins":
        return f"{method} /{parts[0]}" if parts[0] in {"health", "scheduled"} else "other"
    if len(parts) == 1:
        return f"{method} /plugins"
    tail = parts[2:]
    # The first tail segment is an operation name; deeper segments are file paths.
    return f"{method} /plugins/{{id}}" + (f"/{tail[0]}" if tail else "")
