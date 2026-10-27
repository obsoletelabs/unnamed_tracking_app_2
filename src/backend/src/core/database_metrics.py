"""Time SQL cursor execution without retaining SQL, parameters or account labels."""

import asyncio
import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, ExceptionContext, ExecutionContext

_OPERATIONS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "WITH"})
_PENDING = "uta_query_metrics"


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
