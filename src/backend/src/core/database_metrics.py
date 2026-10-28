"""Time SQL cursor execution without retaining SQL, parameters or account labels."""

import asyncio
import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, ExceptionContext, ExecutionContext
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from sqlalchemy.pool import AsyncAdaptedQueuePool

_OPERATIONS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "WITH"})
_PENDING = "uta_query_metrics"


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
            raise
        finally:
            # pylint: disable-next=import-outside-toplevel
            from src.core import metrics

            metrics.DATABASE_CONNECTION_WAIT.observe(time.perf_counter() - started)


class ObservedSyncSession(Session):  # pylint: disable=too-few-public-methods
    """Sync core used by this application's AsyncSession only."""


def _after_commit(_session: Session) -> None:
    # pylint: disable-next=import-outside-toplevel
    from src.core import metrics

    metrics.DATABASE_TRANSACTIONS.labels("commit").inc()


def _after_rollback(_session: Session) -> None:
    # pylint: disable-next=import-outside-toplevel
    from src.core import metrics

    metrics.DATABASE_TRANSACTIONS.labels("rollback").inc()


def _before(*, conn: Connection, statement: str, context: ExecutionContext, **_kwargs: Any) -> None:
    first = statement.lstrip().split(None, 1)
    operation = first[0].upper() if first and first[0].upper() in _OPERATIONS else "OTHER"
    conn.info.setdefault(_PENDING, {})[id(context)] = (operation, time.perf_counter())


def _finish(conn: Connection, context: ExecutionContext, outcome: str) -> None:
    pending = conn.info.get(_PENDING, {})
    observation = pending.pop(id(context), None)
    if observation is not None:
        operation, started = observation
        elapsed = time.perf_counter() - started
        # Avoid an engine/collector import cycle during application startup.
        # pylint: disable-next=import-outside-toplevel
        from src.core import metrics

        metrics.DATABASE_QUERY_DURATION.labels(operation, outcome).observe(elapsed)


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


def install_database_metrics(engine: Engine) -> None:
    """Install once per engine; scrapes never execute SQL through these hooks."""
    if event.contains(engine, "before_cursor_execute", _before):
        return
    event.listen(engine, "before_cursor_execute", _before, named=True)
    event.listen(engine, "after_cursor_execute", _after, named=True)
    event.listen(engine, "handle_error", _error)


def install_session_metrics() -> None:
    """Observe completed session commits and rollbacks once per application session."""
    if not event.contains(ObservedSyncSession, "after_commit", _after_commit):
        event.listen(ObservedSyncSession, "after_commit", _after_commit)
        event.listen(ObservedSyncSession, "after_rollback", _after_rollback)
