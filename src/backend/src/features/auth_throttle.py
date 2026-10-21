"""Shared short-lived authentication reservations, independent of business transactions."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from ipaddress import IPv6Address, ip_address, ip_network

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.metrics import AUTH_DECISIONS
from src.database.auth_limits import AuthLimitSession
from src.features.auth_rate_limits import (
    AuthLimit,
    charge_auth_limits,
    expire_auth_limits,
    refund_auth_limits,
)

logger = logging.getLogger(__name__)
STORE_TIMEOUT_SECONDS = 3


class AuthOperation(str, Enum):
    SETUP = "setup"
    LOGIN = "login"
    LOGIN_ACCOUNT = "login_account"
    OIDC_START = "oidc_start"
    OIDC_CALLBACK = "oidc_callback"
    RECOVERY_REQUEST = "recovery_request"
    RECOVERY_COMPLETE = "recovery_complete"
    INVITATION_ACCEPT = "invitation_accept"
    ACCOUNT_ADMIN = "account_admin"
    ACCOUNT_UPDATE = "account_update"
    API_KEY_CREATE = "api_key_create"
    CREDENTIAL_CHECK = "credential_check"
    DELEGATION = "delegation"
    DELEGATION_POLL = "delegation_poll"
    PUBLIC_SESSION = "public_session"
    STEAM_LOGIN = "steam_login"
    STEAM_POLL = "steam_poll"


class AuthRateLimited(RuntimeError):
    def __init__(self, retry_after: int):
        self.retry_after = max(1, retry_after)
        super().__init__("Too many authentication requests. Try again later.")


class AuthStoreUnavailable(RuntimeError):
    """Failure is closed; never substitute a per-process enforcement counter."""


@dataclass(frozen=True)
class AuthReceipt:
    operation: AuthOperation
    keys: tuple[str, ...] = field(repr=False)


_events: Counter[tuple[AuthOperation, str]] = Counter()
_last_log: dict[tuple[AuthOperation, str], float] = {}


def record_auth_event(operation: AuthOperation, outcome: str) -> None:
    """Process diagnostics have a fixed label set and contain no submitted identities."""
    if outcome not in {"allowed", "limited", "unavailable", "refunded", "busy"}:
        raise ValueError("Unknown authentication diagnostic outcome")
    key = (operation, outcome)
    _events[key] += 1
    AUTH_DECISIONS.labels(operation.value, outcome).inc()
    now = time.monotonic()
    if outcome in {"limited", "unavailable", "busy"} and now - _last_log.get(key, -60) >= 60:
        logger.warning(
            "Authentication check %s: operation=%s count=%d", outcome, operation.value, _events[key]
        )
        _last_log[key] = now


def auth_throttle_statistics() -> dict[str, dict[str, int]]:
    """Per-process aggregate check counts, reset on restart; enforcement remains shared."""
    return {
        operation.value: {
            outcome: count for (flow, outcome), count in _events.items() if flow == operation
        }
        for operation in AuthOperation
        if any(flow == operation for flow, _outcome in _events)
    }


def caller_network(value: str | None) -> str:
    """Share IPv6 privacy addresses within a /64; normalize mapped IPv4 and unknown peers."""
    try:
        address = ip_address(value or "")
    except ValueError:
        return "unknown"
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ip_network((IPv6Address(address.packed), 64), strict=False))
    return str(address)


@asynccontextmanager
async def _limited_session(operation: AuthOperation) -> AsyncIterator[tuple[AsyncSession, int]]:
    try:
        async with asyncio.timeout(STORE_TIMEOUT_SECONDS), AuthLimitSession() as db, db.begin():
            # The database clock gives all workers the same fixed windows. Apply bounded
            # statement/row-lock waits before any counter or cleanup operation.
            now = await db.scalar(
                text(
                    "SELECT floor(extract(epoch FROM clock_timestamp()))::bigint, "
                    "set_config('statement_timeout', '2000', true), "
                    "set_config('lock_timeout', '500', true)"
                )
            )
            if now is None:
                raise AuthStoreUnavailable
            yield db, int(now)
    except (SQLAlchemyError, TimeoutError, AuthStoreUnavailable):
        record_auth_event(operation, "unavailable")
        raise AuthStoreUnavailable from None


async def reserve_auth_attempt(
    operation: AuthOperation, limits: tuple[AuthLimit, ...]
) -> AuthReceipt:
    if not limits:
        raise ValueError("Authentication checks require at least one quota")
    try:
        async with _limited_session(operation) as (db, now):
            await expire_auth_limits(db, now)
            decision = await charge_auth_limits(
                db, "auth.v1." + operation.value, limits, now, reserve=True
            )
            if not decision.allowed:
                raise AuthRateLimited(decision.retry_after)
    except AuthRateLimited:
        record_auth_event(operation, "limited")
        raise
    record_auth_event(operation, "allowed")
    return AuthReceipt(operation, decision.keys)


async def release_auth_attempts(receipts: tuple[AuthReceipt, ...]) -> None:
    if not receipts:
        return
    async with _limited_session(receipts[0].operation) as (db, _now):
        await refund_auth_limits(db, tuple(key for receipt in receipts for key in receipt.keys))
    for receipt in receipts:
        record_auth_event(receipt.operation, "refunded")


async def reserve_credential_attempt(caller: str | None) -> AuthReceipt:
    return await reserve_auth_attempt(
        AuthOperation.CREDENTIAL_CHECK,
        (AuthLimit("failed-caller", caller_network(caller), 300, 60),),
    )
