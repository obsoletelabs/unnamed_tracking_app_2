"""Fair, bounded discovery within the existing scheduler; no second worker."""

import asyncio
import logging
from uuid import UUID

from sqlalchemy import select

from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notifications import generate_for_user

logger = logging.getLogger(__name__)
_cursor: dict[str, UUID | None] = {"last_user": None}
MAX_USERS_PER_TICK = 4


async def scan_notification_users() -> int:
    """Cursor advances across failures; restarts safely rediscover through receipts."""
    async with SessionLocal() as db:
        statement = select(User.id).where(User.is_active.is_(True)).order_by(User.id)
        last_user = _cursor["last_user"]
        if last_user is not None:
            statement = statement.where(User.id > last_user)
        user_ids = list(await db.scalars(statement.limit(MAX_USERS_PER_TICK)))
    if not user_ids:
        _cursor["last_user"] = None
        return 0
    created = 0
    for user_id in user_ids:
        _cursor["last_user"] = user_id
        try:
            async with SessionLocal() as db:
                created += await asyncio.wait_for(generate_for_user(db, user_id), timeout=10)
        # An individual account's discovery must not starve later accounts.
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Notification discovery failed for user %s", user_id)
    return created
