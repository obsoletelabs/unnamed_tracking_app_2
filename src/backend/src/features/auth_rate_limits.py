"""Bound recovery requests across workers without retaining submitted identifiers."""

import hashlib
import hmac
from dataclasses import dataclass, field

from sqlalchemy import case, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.plugin_auth_flow import AuthRequestLimit


@dataclass(frozen=True)
class AuthLimit:
    category: str
    identity: str = field(repr=False)
    ceiling: int
    interval: int

    def __post_init__(self) -> None:
        if self.ceiling < 1 or self.interval < 1:
            raise ValueError("Authentication quota thresholds and windows must be positive")

    @property
    def maximum(self) -> int:
        return min(
            2147483646, max(1, (self.ceiling * settings.AUTH_RATE_LIMIT_PERCENT + 99) // 100)
        )


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    retry_after: int = 0
    keys: tuple[str, ...] = field(default=(), repr=False)


def limit_key(purpose: str, limit: AuthLimit, now: int) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"{purpose}/{limit.category}/{now // limit.interval}/{limit.identity}".encode(),
        hashlib.sha256,
    ).hexdigest()


async def expire_auth_limits(db: AsyncSession, now: int) -> None:
    expired = (
        select(AuthRequestLimit.key)
        .where(AuthRequestLimit.expires_at <= now)
        .order_by(AuthRequestLimit.expires_at, AuthRequestLimit.key)
        .limit(100)
        .with_for_update(skip_locked=True)
    )
    await db.execute(delete(AuthRequestLimit).where(AuthRequestLimit.key.in_(expired)))


async def charge_auth_limits(
    db: AsyncSession, purpose: str, limits: tuple[AuthLimit, ...], now: int, *, reserve: bool
) -> LimitDecision:
    """Use one ordered atomic upsert per bucket; the caller owns commit/rollback.

    Reservations must roll back the whole transaction on denial. Legacy bool callers
    retain their existing semantics: denied requests also charge their other buckets.
    """
    keyed = sorted(
        ((limit_key(purpose, limit, now), limit) for limit in limits), key=lambda item: item[0]
    )
    if len({key for key, _limit in keyed}) != len(keyed):
        raise ValueError("Duplicate authentication quota bucket")
    retry_after = 0
    for key, limit in keyed:
        expires = (now // limit.interval + 1) * limit.interval
        maximum = limit.maximum
        statement = pg_insert(AuthRequestLimit).values(key=key, count=1, expires_at=expires)
        if reserve:
            statement = statement.on_conflict_do_update(
                index_elements=["key"],
                set_={"count": AuthRequestLimit.count + 1},
                where=AuthRequestLimit.count < maximum,
            )
        else:
            # Keep counters bounded even when an already denied legacy caller repeats.
            statement = statement.on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "count": case(
                        (AuthRequestLimit.count < maximum + 1, AuthRequestLimit.count + 1),
                        else_=maximum + 1,
                    )
                },
            )
        count = await db.scalar(statement.returning(AuthRequestLimit.count))
        if count is None or count > maximum:
            retry_after = max(retry_after, expires - now)
    return LimitDecision(retry_after == 0, retry_after, tuple(key for key, _limit in keyed))


async def refund_auth_limits(db: AsyncSession, keys: tuple[str, ...]) -> None:
    # Use the same lock order as allocation, including when receipts share a caller bucket.
    for key in sorted(set(keys)):
        await db.execute(
            update(AuthRequestLimit)
            .where(AuthRequestLimit.key == key, AuthRequestLimit.count > 0)
            .values(count=AuthRequestLimit.count - 1)
        )


async def allow_recovery_request(db: AsyncSession, identifier: str, caller: str) -> bool:
    return await allow_auth_request(
        db, "reset", (("caller", caller, 30), ("account", identifier, 5))
    )


async def allow_auth_request(db, purpose, limits, *, interval=3600):
    return (await auth_request_decision(db, purpose, limits, interval=interval)).allowed


async def auth_request_decision(db, purpose, limits, *, interval=3600) -> LimitDecision:
    now = int(await db.scalar(text("SELECT floor(extract(epoch FROM clock_timestamp()))::bigint")))
    await expire_auth_limits(db, now)
    decision = await charge_auth_limits(
        db,
        purpose,
        tuple(
            AuthLimit(category, identity, ceiling, interval)
            for category, identity, ceiling in limits
        ),
        now,
        reserve=False,
    )
    return decision
