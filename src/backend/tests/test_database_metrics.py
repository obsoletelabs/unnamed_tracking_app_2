"""Database timers count executions and never expose statements or parameters."""

import asyncio

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeoutError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import AsyncAdaptedQueuePool

from src.core import metrics
from src.core.database_metrics import (
    ObservedAsyncPool,
    ObservedSyncSession,
    install_database_metrics,
    install_session_metrics,
)
from src.database.session import SessionLocal


def count(operation, outcome):
    return (
        metrics.REGISTRY.get_sample_value(
            "uta_database_query_duration_seconds_count",
            {"operation": operation, "outcome": outcome},
        )
        or 0
    )


def test_queries_errors_and_idempotent_installation():
    engine = create_engine("sqlite://")
    install_database_metrics(engine)
    install_database_metrics(engine)
    ok, error = count("SELECT", "ok"), count("SELECT", "error")
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT :private_value"), {"private_value": 7}) == 7
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT private_unknown_column"))
        assert count("SELECT", "ok") == ok + 1
        assert count("SELECT", "error") == error + 1
    finally:
        engine.dispose()


async def test_cancelled_query_has_its_own_outcome():
    before = count("SELECT", "cancelled")
    async with SessionLocal() as db:
        await db.execute(text("SELECT 1"))
        task = asyncio.create_task(db.execute(text("SELECT pg_sleep(10)")))
        # Warm up first so this cancellation interrupts execution, not pool checkout.
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert count("SELECT", "cancelled") == before + 1


def test_session_transitions_count_commits_and_rollbacks_once():
    install_session_metrics()
    install_session_metrics()
    before_commit = metrics.REGISTRY.get_sample_value(
        "uta_database_transactions_total", {"outcome": "commit"}
    ) or 0
    before_rollback = metrics.REGISTRY.get_sample_value(
        "uta_database_transactions_total", {"outcome": "rollback"}
    ) or 0
    engine = create_engine("sqlite://")
    try:
        with sessionmaker(bind=engine, class_=ObservedSyncSession)() as db:
            db.execute(text("SELECT 1"))
            db.commit()
            db.execute(text("SELECT 1"))
            db.rollback()
        assert metrics.REGISTRY.get_sample_value(
            "uta_database_transactions_total", {"outcome": "commit"}
        ) == before_commit + 1
        assert metrics.REGISTRY.get_sample_value(
            "uta_database_transactions_total", {"outcome": "rollback"}
        ) == before_rollback + 1
    finally:
        engine.dispose()


def test_pool_timeout_and_wait_are_measured(monkeypatch):
    pool = ObservedAsyncPool(lambda: object())
    before_wait = metrics.REGISTRY.get_sample_value("uta_database_connection_wait_seconds_count") or 0
    before_timeout = metrics.REGISTRY.get_sample_value(
        "uta_database_pool_timeouts_total", {"reason": "exhausted"}
    ) or 0
    monkeypatch.setattr(AsyncAdaptedQueuePool, "_do_get", lambda _self: object())
    pool._do_get()  # pylint: disable=protected-access

    def timeout(_self):
        raise PoolTimeoutError("private pool state")

    monkeypatch.setattr(AsyncAdaptedQueuePool, "_do_get", timeout)
    with pytest.raises(PoolTimeoutError):
        pool._do_get()  # pylint: disable=protected-access
    assert metrics.REGISTRY.get_sample_value("uta_database_connection_wait_seconds_count") == (
        before_wait + 2
    )
    assert metrics.REGISTRY.get_sample_value(
        "uta_database_pool_timeouts_total", {"reason": "exhausted"}
    ) == before_timeout + 1
