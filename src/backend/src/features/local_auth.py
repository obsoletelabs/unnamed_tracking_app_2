"""Refund successful login reservations while retaining only failed/in-flight attempts."""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from threading import BoundedSemaphore
from uuid import UUID

from src.core.auth import hash_password
from src.core.config import settings
from src.core.password_work import PasswordWorkBusy
from src.features.auth_rate_limits import AuthLimit
from src.features.auth_throttle import (
    AuthOperation,
    AuthReceipt,
    caller_network,
    release_auth_attempts,
    reserve_auth_attempt,
)

DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
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
