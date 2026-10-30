"""Time SQL cursor execution without retaining SQL, parameters or account labels."""

import asyncio
import re
import time
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, ExceptionContext, ExecutionContext
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.sql.compiler import Compiled

_OPERATIONS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "WITH"})
_PENDING = "uta_query_metrics"
_POOLS = frozenset({"application", "authentication"})
_TABLE = re.compile(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:"?\w+"?\.)?"?(\w+)"?', re.IGNORECASE)
_QUERY_GROUPS: WeakKeyDictionary[Compiled, str] = WeakKeyDictionary()


def _query_group(statement: str, context: ExecutionContext) -> str:
    compiled = context.compiled
    if compiled is not None and compiled in _QUERY_GROUPS:
        return _QUERY_GROUPS[compiled]
    group = "control" if not _TABLE.search(statement) else "other"
    for match in _TABLE.finditer(statement):
        table = match.group(1).lower()
        if table == "auth_request_limits":
            group = "auth_quota"
        elif table in {"users", "user_sessions", "user_api_keys", "auth_recovery_tokens"}:
            group = "credentials"
        elif table.startswith("plugin_"):
            group = "plugins"
        elif table == "notifications" or table.startswith("notification_"):
            group = "notifications"
        elif table.startswith(("game", "achievement")):
            group = "games"
        elif table.startswith(("media", "movie", "tv_", "anime", "episode")):
            group = "media"
        elif table.endswith(("settings", "preferences")) or table == "app_integration_settings":
            group = "settings"
        if group != "other":
            break
    if compiled is not None:
        _QUERY_GROUPS[compiled] = group
    return group


class ObservedAsyncPool(AsyncAdaptedQueuePool):
    """Measure real pool acquisition at the existing engine boundary."""

    def _do_get(self):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        try:
            return super()._do_get()
        except PoolTimeoutError:
            # pylint: disable-next=import-outside-toplevel
            from src.core import metrics

            metrics.DATABASE_POOL_TIMEOUTS.labels("exhausted").inc()
            if metrics.metrics_enabled():
                pool = self.logging_name if self.logging_name in _POOLS else "application"
                metrics.DATABASE_NAMED_POOL_TIMEOUTS.labels(pool).inc()
            raise
        finally:
            # pylint: disable-next=import-outside-toplevel
            from src.core import metrics

            seconds = time.perf_counter() - started
            metrics.DATABASE_CONNECTION_WAIT.observe(seconds)
            if metrics.metrics_enabled():
                from src.core import request_metrics  # pylint: disable=import-outside-toplevel

                pool = self.logging_name if self.logging_name in _POOLS else "application"
                metrics.DATABASE_POOL_WAIT.labels(pool).observe(seconds)
                request_metrics.observe_phase("pool." + pool, seconds)


class ObservedSyncSession(Session):  # pylint: disable=too-few-public-methods
    """Sync core used by this application's AsyncSession only."""


def _before_commit(session: Session) -> None:
    from src.core import metrics  # pylint: disable=import-outside-toplevel

    if metrics.metrics_enabled():
        session.info["uta_commit_started"] = time.perf_counter()


def _after_commit(session: Session) -> None:
    # pylint: disable-next=import-outside-toplevel
    from src.core import metrics, request_metrics

    metrics.DATABASE_TRANSACTIONS.labels("commit").inc()
    started = session.info.pop("uta_commit_started", None)
    if started is not None:
        seconds = time.perf_counter() - started
        options = session.bind.get_execution_options() if session.bind is not None else {}
        name = options.get("uta_metrics_pool", "application")
        pool = name if name in _POOLS else "application"
        metrics.DATABASE_COMMIT_DURATION.labels(pool).observe(seconds)
        request_metrics.observe_phase("commit." + pool, seconds)


def _after_rollback(_session: Session) -> None:
    # pylint: disable-next=import-outside-toplevel
    from src.core import metrics

    metrics.DATABASE_TRANSACTIONS.labels("rollback").inc()
    _session.info.pop("uta_commit_started", None)


def _before(*, conn: Connection, statement: str, context: ExecutionContext, **_kwargs: Any) -> None:
    first = statement.lstrip().split(None, 1)
    operation = first[0].upper() if first and first[0].upper() in _OPERATIONS else "OTHER"
    from src.core import metrics  # pylint: disable=import-outside-toplevel

    pool = context.execution_options.get("uta_metrics_pool", "application")
    pool = pool if pool in _POOLS else "application"
    group = _query_group(statement, context) if metrics.metrics_enabled() else None
    conn.info.setdefault(_PENDING, {})[id(context)] = (operation, time.perf_counter(), pool, group)


def _finish(conn: Connection, context: ExecutionContext, outcome: str) -> None:
    pending = conn.info.get(_PENDING, {})
    observation = pending.pop(id(context), None)
    if observation is not None:
        operation, started, pool, group = observation
        elapsed = time.perf_counter() - started
        # Avoid an engine/collector import cycle during application startup.
        # pylint: disable-next=import-outside-toplevel
        from src.core import metrics

        metrics.DATABASE_QUERY_DURATION.labels(operation, outcome).observe(elapsed)
        if group is not None:
            from src.core import request_metrics  # pylint: disable=import-outside-toplevel

            metrics.DATABASE_GROUP_DURATION.labels(pool, group, operation, outcome).observe(elapsed)
            request_metrics.observe_query(pool, group, elapsed)


def _after(*, conn: Connection, context: ExecutionContext, **_kwargs: Any) -> None:
    _finish(conn, context, "ok")


def _error(exception_context: ExceptionContext) -> None:
    if exception_context.connection is not None and exception_context.execution_context is not None:
        outcome = (
            "cancelled"
            if isinstance(exception_context.original_exception, asyncio.CancelledError)
            else "error"
        )
        _finish(exception_context.connection, exception_context.execution_context, outcome)


def install_database_metrics(engine: Engine, *, pool: str = "application") -> None:
    """Install once per engine; scrapes never execute SQL through these hooks."""
    if pool not in _POOLS:
        raise ValueError("Unknown metrics pool")
    if event.contains(engine, "before_cursor_execute", _before):
        return
    engine.update_execution_options(uta_metrics_pool=pool)
    event.listen(engine, "before_cursor_execute", _before, named=True)
    event.listen(engine, "after_cursor_execute", _after, named=True)
    event.listen(engine, "handle_error", _error)


def install_session_metrics() -> None:
    """Observe completed session commits and rollbacks once per application session."""
    if not event.contains(ObservedSyncSession, "after_commit", _after_commit):
        event.listen(ObservedSyncSession, "before_commit", _before_commit)
        event.listen(ObservedSyncSession, "after_commit", _after_commit)
        event.listen(ObservedSyncSession, "after_rollback", _after_rollback)
