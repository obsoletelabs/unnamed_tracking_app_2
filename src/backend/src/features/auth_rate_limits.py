"""Bound recovery requests across workers without retaining submitted identifiers."""

import hashlib
import hmac
import time

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.plugin_auth_flow import AuthRequestLimit


async def allow_recovery_request(db: AsyncSession, identifier: str, caller: str) -> bool:
    now = int(time.time())
    await db.execute(delete(AuthRequestLimit).where(AuthRequestLimit.expires_at <= now))
    allowed = True
    for category, identity, ceiling in (("caller", caller, 30), ("account", identifier, 5)):
        key = hmac.new(
            settings.SECRET_KEY.encode(),
            f"reset/{category}/{now // 3600}/{identity}".encode(),
            hashlib.sha256,
        ).hexdigest()
        count = await db.scalar(
            pg_insert(AuthRequestLimit)
            .values(key=key, count=1, expires_at=(now // 3600 + 1) * 3600)
            .on_conflict_do_update(
                index_elements=["key"], set_={"count": AuthRequestLimit.count + 1}
            )
            .returning(AuthRequestLimit.count)
        )
        allowed = allowed and count is not None and count <= ceiling
    return allowed
