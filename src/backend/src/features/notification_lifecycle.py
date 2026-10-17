"""User inbox lifecycle, independent of delivery and durable dedupe state."""

import time
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification import Notification
from src.database.models.notification_delivery import ACTIVE_DELIVERY_STATUSES, NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_story import (
    NotificationLiveLink,
    NotificationStory,
    NotificationStoryRevision,
)
from src.database.models.notification_verification import NotificationVerification
from src.features.notification_audit import change_deliveries, change_notices, record_notices


async def retire_destination_work(db: AsyncSession, destination_id: UUID, reason: str) -> None:
    """Withdraw queued work and outstanding challenges without deleting history."""
    await db.execute(
        update(NotificationVerification)
        .where(NotificationVerification.destination_id == destination_id)
        .values(encrypted_code=None, used_at=int(time.time()))
    )
    await change_deliveries(
        db,
        update(NotificationDelivery)
        .where(
            NotificationDelivery.destination_id == destination_id,
            NotificationDelivery.status.in_(ACTIVE_DELIVERY_STATUSES),
        )
        .values(status="suppressed", last_error=reason, claim_token=None, lease_until=None),
    )


async def invalidate_endpoint(
    db: AsyncSession, destination: NotificationDestination, reason: str = "endpoint_changed"
) -> None:
    """An endpoint revision withdraws proof, consent and all unsent work."""
    destination.revision += 1
    destination.verified_revision = None
    destination.verification_method = None
    destination.verification_revoked_at = int(time.time())
    destination.recovery_allowed = False
    destination.media_consent_revision = None
    destination.media_consent_at = None
    await retire_destination_work(db, destination.id, reason)


async def retire_destination_type_work(
    db: AsyncSession, destination_id: UUID, event_type: str
) -> None:
    """A type opt-out preserves proof and unrelated queued destination work."""
    await change_deliveries(
        db,
        update(NotificationDelivery)
        .where(
            NotificationDelivery.destination_id == destination_id,
            NotificationDelivery.notification_id.in_(
                select(Notification.id).where(Notification.event_type == event_type)
            ),
            NotificationDelivery.status.in_(ACTIVE_DELIVERY_STATUSES),
        )
        .values(
            status="suppressed",
            last_error="email_type_unsubscribed",
            claim_token=None,
            lease_until=None,
        ),
    )


def visible_inbox(user_id: UUID):
    return (
        (Notification.user_id == user_id)
        & Notification.inbox_visible.is_(True)
        & Notification.dismissed_at.is_(None)
        & Notification.deleted_at.is_(None)
    )


async def dismiss(db: AsyncSession, user_id: UUID, notification_id: UUID) -> bool:
    count = await change_notices(
        db,
        update(Notification)
        .where(Notification.id == notification_id, visible_inbox(user_id))
        .values(dismissed_at=int(time.time())),
        "dismissed",
    )
    return bool(count)


async def delete_notice(db: AsyncSession, user_id: UUID, notification_id: UUID) -> bool:
    notification = await db.scalar(
        select(Notification)
        .where(Notification.id == notification_id, Notification.user_id == user_id)
        .with_for_update()
    )
    if notification is None:
        return False
    if notification.deleted_at is None:
        await record_notices(db, [notification], "deleted")
    notification.deleted_at = int(time.time())
    notification.inbox_visible = False
    notification.title = ""
    notification.body = ""
    notification.poster_url = None
    notification.public_title = None
    notification.public_body = None
    # Revision history keeps its links; only the erased item's content goes.
    await db.execute(
        update(NotificationStoryRevision)
        .where(NotificationStoryRevision.notification_id == notification_id)
        .values(title=None, body=None, content=None)
    )
    if notification.story_id is not None:
        # A live link shows the story's latest item; once that item is erased the link is dead.
        await db.execute(
            update(NotificationLiveLink)
            .where(
                NotificationLiveLink.story_id == notification.story_id,
                NotificationLiveLink.revoked_at.is_(None),
                NotificationLiveLink.story_id.in_(
                    select(NotificationStory.id).where(
                        NotificationStory.id == notification.story_id,
                        NotificationStory.revision == notification.story_revision,
                    )
                ),
            )
            .values(revoked_at=int(time.time()))
        )
    await change_deliveries(
        db,
        update(NotificationDelivery)
        .where(
            NotificationDelivery.notification_id == notification_id,
            NotificationDelivery.status.in_(ACTIVE_DELIVERY_STATUSES),
        )
        .values(status="cancelled", claim_token=None, lease_until=None),
    )
    return True
