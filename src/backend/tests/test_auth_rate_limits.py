"""Real PostgreSQL quota behavior, independent workers and bounded password computation."""

import asyncio
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack
from pathlib import Path
from threading import BoundedSemaphore, Event
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import OperationalError

from src.core import password_work
from src.database.auth_limits import auth_limit_engine
from src.database.models.plugin_auth_flow import AuthRequestLimit

# The shared asset fixture registers Game; complete its User relationship before ORM queries.
from src.database.models.user import User  # noqa: F401
from src.database.session import SessionLocal
from src.features import auth_throttle
from src.features.auth_rate_limits import AuthLimit, allow_auth_request
from src.features.auth_throttle import (
    AuthOperation,
    AuthRateLimited,
    AuthStoreUnavailable,
    caller_network,
    release_auth_attempts,
    reserve_auth_attempt,
)

# A stable test window avoids a minute/day boundary racing concurrent assertions.
INTERVAL = 10**12


@pytest.fixture(autouse=True)
async def clean_quotas():
    async with SessionLocal() as db:
        await db.execute(delete(AuthRequestLimit))
        await db.commit()
    yield
    async with SessionLocal() as db:
        await db.execute(delete(AuthRequestLimit))
        await db.commit()


def bucket(ceiling=3, identity=None):
    return AuthLimit("test-account", identity or uuid4().hex, ceiling, INTERVAL)


@pytest.mark.asyncio
async def test_concurrent_sessions_share_one_atomic_threshold():
    limit = bucket()

    async def attempt():
        try:
            await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (limit,))
            return "allowed"
        except AuthRateLimited as error:
            assert 1 <= error.retry_after <= INTERVAL
            return "limited"

    outcomes = await asyncio.gather(*(attempt() for _ in range(12)))
    assert outcomes.count("allowed") == 3
    assert outcomes.count("limited") == 9
    async with SessionLocal() as db:
        rows = list(await db.scalars(select(AuthRequestLimit)))
    assert len(rows) == 1 and rows[0].count == 3
    assert len(rows[0].key) == 64 and limit.identity not in rows[0].key


@pytest.mark.asyncio
async def test_denied_multi_bucket_reservation_rolls_back_other_buckets():
    full, other = bucket(1), bucket(20)
    receipt = await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (full,))
    with pytest.raises(AuthRateLimited):
        await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (full, other))
    async with SessionLocal() as db:
        assert set(await db.scalars(select(AuthRequestLimit.key))) == set(receipt.keys)


@pytest.mark.asyncio
async def test_success_refund_and_expiration_allow_recovery():
    limit = bucket(1)
    receipt = await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (limit,))
    with pytest.raises(AuthRateLimited):
        await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (limit,))
    await release_auth_attempts((receipt,))
    second = await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (limit,))
    async with SessionLocal() as db:
        await db.execute(
            update(AuthRequestLimit)
            .where(AuthRequestLimit.key.in_(second.keys))
            .values(expires_at=0)
        )
        await db.commit()
    await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (limit,))
    async with SessionLocal() as db:
        row = await db.scalar(select(AuthRequestLimit))
        assert row.count == 1 and row.expires_at > time.time()


@pytest.mark.asyncio
async def test_legacy_boolean_limits_keep_their_contract_and_bound_denied_counts():
    permitted = []
    async with SessionLocal() as db:
        for _index in range(4):
            permitted.append(
                await allow_auth_request(
                    db, "legacy-test", (("account", "private", 1),), interval=INTERVAL
                )
            )
            await db.commit()
        row = await db.scalar(select(AuthRequestLimit))
        assert row.count == 2
    assert permitted == [True, False, False, False]


@pytest.mark.asyncio
async def test_locked_storage_fails_closed_then_recovers():
    limit = bucket(2)
    receipt = await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (limit,))
    async with SessionLocal() as locker, locker.begin():
        await locker.scalar(
            select(AuthRequestLimit)
            .where(AuthRequestLimit.key == receipt.keys[0])
            .with_for_update()
        )
        with pytest.raises(AuthStoreUnavailable):
            await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (limit,))
    await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (limit,))


@pytest.mark.asyncio
async def test_database_errors_never_escape_as_credentials_or_statements(monkeypatch, caplog):
    def unavailable():
        raise OperationalError(
            "secret-db-url", {"password": "secret-not-for-logs"}, RuntimeError("private")
        )

    monkeypatch.setattr(auth_throttle, "AuthLimitSession", unavailable)
    monkeypatch.setattr(auth_throttle, "_last_log", {})
    with caplog.at_level(logging.WARNING), pytest.raises(AuthStoreUnavailable) as raised:
        await reserve_auth_attempt(AuthOperation.OIDC_START, (bucket(),))
    assert not str(raised.value)
    assert "operation=oidc_start" in caplog.text
    assert "secret-not-for-logs" not in caplog.text and "secret-db-url" not in caplog.text


@pytest.mark.asyncio
async def test_storage_wait_is_bounded(monkeypatch):
    class SlowSession:
        async def __aenter__(self):
            await asyncio.sleep(30)

        async def __aexit__(self, *_arguments):
            return None

    monkeypatch.setattr(auth_throttle, "AuthLimitSession", SlowSession)
    monkeypatch.setattr(auth_throttle, "STORE_TIMEOUT_SECONDS", 0.02)
    started = time.monotonic()
    with pytest.raises(AuthStoreUnavailable):
        await reserve_auth_attempt(AuthOperation.OIDC_START, (bucket(),))
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_separate_processes_with_skewed_local_clocks_share_storage():
    identity = uuid4().hex
    script = """
import asyncio, sys, time
from src.features.auth_rate_limits import AuthLimit
from src.features.auth_throttle import AuthOperation, AuthRateLimited, reserve_auth_attempt
time.time = lambda: float(sys.argv[2])
async def attempt():
    try:
        await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (AuthLimit('worker', sys.argv[1], 1, 10**12),))
        print('allowed')
    except AuthRateLimited:
        print('limited')
asyncio.run(attempt())
"""

    async def worker(clock):
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            identity,
            str(clock),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
            cwd=Path(__file__).parents[1],
        )
        stdout, stderr = await asyncio.wait_for(child.communicate(), 20)
        assert child.returncode == 0, stderr.decode()
        return stdout.decode().strip().splitlines()[-1]

    assert sorted(await asyncio.gather(worker(100), worker(10**10))) == ["allowed", "limited"]


@pytest.mark.asyncio
async def test_quota_checks_do_not_need_a_free_business_connection():
    async with AsyncExitStack() as stack:
        for _index in range(15):  # Existing business pool: five connections plus ten overflow.
            session = await stack.enter_async_context(SessionLocal())
            await session.execute(text("SELECT 1"))
        await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (bucket(),))


@pytest.mark.asyncio
async def test_short_healthy_burst_waits_for_quota_capacity_within_total_deadline():
    async with AsyncExitStack() as stack:
        for _index in range(2):
            await stack.enter_async_context(auth_limit_engine.connect())
        pending = asyncio.create_task(
            reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, (bucket(),))
        )
        await asyncio.sleep(0.65)
        # A healthy short burst must not fail at the former half-second checkout bound.
        assert not pending.done()
    assert await asyncio.wait_for(pending, 2)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("192.0.2.1", "192.0.2.1"),
        ("::ffff:192.0.2.1", "192.0.2.1"),
        ("2001:db8:abcd:1234::1", "2001:db8:abcd:1234::/64"),
        ("2001:db8:abcd:1234:ffff:ffff:ffff:ffff", "2001:db8:abcd:1234::/64"),
        (None, "unknown"),
        ("invalid", "unknown"),
    ],
)
def test_address_buckets_account_for_nat_and_ipv6_privacy_addresses(value, expected):
    assert caller_network(value) == expected


@pytest.mark.asyncio
async def test_cancelled_password_task_keeps_its_slot_until_thread_finishes(monkeypatch):
    started, release = Event(), Event()
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(password_work, "_executor", executor)
    monkeypatch.setattr(password_work, "_slots", BoundedSemaphore(1))

    def slow_job():
        started.set()
        release.wait(5)
        return True

    task = asyncio.create_task(password_work.run_password_job(slow_job))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        with pytest.raises(password_work.PasswordWorkBusy):
            await password_work.run_password_job(lambda: True)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(password_work.PasswordWorkBusy):
            await password_work.run_password_job(lambda: True)
        release.set()
        deadline = time.monotonic() + 2
        while True:
            try:
                assert await password_work.run_password_job(lambda: True)
                break
            except password_work.PasswordWorkBusy:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.01)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
