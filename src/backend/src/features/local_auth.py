"""Refund successful login reservations while retaining only failed/in-flight attempts."""

import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from threading import BoundedSemaphore
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import hash_password, is_legacy_hash, legacy_timing_hash, verify_password
from src.core.config import settings
from src.core.password_work import PasswordWorkBusy, run_password_job
from src.database.models.user import User
from src.features.auth_rate_limits import AuthLimit
from src.features.auth_throttle import (
    AuthOperation,
    AuthReceipt,
    caller_network,
    release_auth_attempts,
    reserve_auth_attempt,
)

DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
_DUMMY_LEGACY_HASH = legacy_timing_hash(secrets.token_urlsafe(32))
_LEGACY_CHECK_SECONDS = 300
_legacy_check: dict[str, float | bool] = {"until": 0.0, "remain": True}
# This gate bounds handlers holding User rows/business connections. The separate
# password-work gate continues to bound threads after HTTP cancellation.
_login_slots = BoundedSemaphore(min(4, settings.AUTH_PASSWORD_WORKERS))


def _account_limits(category: str, identity: str) -> tuple[AuthLimit, ...]:
    return (
        AuthLimit(category + "-minute", identity, settings.AUTH_LOGIN_ACCOUNT_PER_MINUTE, 60),
        AuthLimit(category + "-long", identity, settings.AUTH_LOGIN_ACCOUNT_PER_15_MINUTES, 900),
    )


@dataclass
class LoginReservation:
    receipts: list[AuthReceipt] = field(default_factory=list, repr=False)
    failed: bool = False

    async def for_account(self, user_id: UUID) -> None:
        self.receipts.append(
            await reserve_auth_attempt(
                AuthOperation.LOGIN_ACCOUNT,
                _account_limits("account", str(user_id)),
            )
        )


@asynccontextmanager
async def local_login_attempt(
    identifier: str, caller: str | None
) -> AsyncIterator[LoginReservation]:
    # A semaphore context manager would queue callers instead of refusing excess work.
    if not _login_slots.acquire(blocking=False):  # pylint: disable=consider-using-with
        raise PasswordWorkBusy
    reservation = LoginReservation()
    try:
        limits = _account_limits("submitted", identifier.strip().lower()) + (
            AuthLimit(
                "failed-caller",
                caller_network(caller),
                settings.AUTH_LOGIN_IP_FAILURES_PER_15_MINUTES,
                900,
            ),
        )
        reservation.receipts.append(await reserve_auth_attempt(AuthOperation.LOGIN_ACCOUNT, limits))
        yield reservation
    finally:
        try:
            if not reservation.failed:
                await release_auth_attempts(tuple(reservation.receipts))
        finally:
            _login_slots.release()


async def _legacy_hashes_remain(db: AsyncSession, *, known_legacy: bool) -> bool:
    """Whether any account still has a scrypt hash, cached for a few minutes per process."""
    now = time.monotonic()
    if known_legacy:
        _legacy_check.update(until=now + _LEGACY_CHECK_SECONDS, remain=True)
    elif now >= float(_legacy_check["until"]):
        remain = bool(
            await db.scalar(select(exists().where(User.password_hash.startswith("scrypt$"))))
        )
        _legacy_check.update(until=now + _LEGACY_CHECK_SECONDS, remain=remain)
    return bool(_legacy_check["remain"])


async def verify_login_password(
    db: AsyncSession,
    password: str,
    stored_hash: str | None,
    verify: Callable[[str, str], bool] | None = None,
) -> bool:
    """Check a sign-in password so existing and nonexistent accounts take the same time.

    While any scrypt account remains, every attempt spends one Argon2id and one scrypt
    verification: the real or placeholder record's own algorithm plus a throwaway record of the
    other. Once every account is on Argon2id there is no second step. Only the first result counts.
    """
    check = verify or verify_password
    stored = stored_hash if stored_hash is not None else DUMMY_PASSWORD_HASH
    legacy = is_legacy_hash(stored)
    matches = await run_password_job(check, password, stored)
    if await _legacy_hashes_remain(db, known_legacy=legacy):
        balance = DUMMY_PASSWORD_HASH if legacy else _DUMMY_LEGACY_HASH
        await run_password_job(check, password, balance)
    return matches
