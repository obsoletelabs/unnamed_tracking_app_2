"""User inbox lifecycle, independent of delivery and durable dedupe state."""

import time
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_verification import NotificationVerification


async def retire_destination_work(db: AsyncSession, destination_id: UUID, reason: str) -> None:
    """Withdraw queued work and outstanding challenges without deleting history."""
    await db.execute(
        update(NotificationVerification)
        .where(NotificationVerification.destination_id == destination_id)
        .values(encrypted_code=None, used_at=int(time.time()))
    )
    await db.execute(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.destination_id == destination_id,
            NotificationDelivery.status.in_(("pending", "processing", "retry_wait")),
        )
        .values(status="suppressed", last_error=reason, claim_token=None, lease_until=None)
    )


def visible_inbox(user_id: UUID):
    return (
        (Notification.user_id == user_id)
        & Notification.inbox_visible.is_(True)
        & Notification.dismissed_at.is_(None)
        & Notification.deleted_at.is_(None)
    )


async def dismiss(db: AsyncSession, user_id: UUID, notification_id: UUID) -> bool:
    result = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, visible_inbox(user_id))
        .values(dismissed_at=int(time.time()))
    )
    return bool(result.rowcount)


async def delete_notice(db: AsyncSession, user_id: UUID, notification_id: UUID) -> bool:
    notification = await db.scalar(
        select(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
        .with_for_update()
    )
    if notification is None:
        return False
    notification.deleted_at = int(time.time())
    notification.inbox_visible = False
    notification.title = ""
    notification.body = ""
    notification.poster_url = None
    notification.public_title = None
    notification.public_body = None
    await db.execute(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.notification_id == notification_id,
            NotificationDelivery.status.in_(("pending", "processing", "retry_wait")),
        )
        .values(status="cancelled", claim_token=None, lease_until=None)
    )
    return True
