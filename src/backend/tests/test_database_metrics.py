"""Database timers count executions and never expose statements or parameters."""

import asyncio

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from src.core import metrics
from src.core.database_metrics import install_database_metrics
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
