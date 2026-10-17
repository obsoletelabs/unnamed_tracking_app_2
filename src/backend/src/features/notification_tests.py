"""Generic provider diagnostics without account content or verification authority."""

import time
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.user import User
from src.features.notification_diagnostic_fixtures import VARIANTS
from src.features.notification_enrollment import EnrollmentError
from src.features.notification_policy import Trust, select_projection
from src.features.notification_providers.base import NotificationMessage
from src.features.notification_providers.delivery import ensure_deliveries
from src.features.notification_providers.smtp import deliver_mail
from src.features.smtp_configuration import normalize_email, smtp_configuration

MAX_TEST_BATCH = 5
TEST_EVENT = "notification.provider.test"
TEST_TITLE = "Test notification"
TEST_BODY = "Example release: A new episode is available. This is a delivery test."


async def reserve_test(db: AsyncSession, user_id: UUID) -> UUID:
    """Serialize the existing owner's row and keep payload-free test quota receipts."""
    owner = await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
    if owner is None:
        raise EnrollmentError("Account unavailable", 404)
    now = int(time.time())
    recent = await db.scalar(
        select(func.count())  # pylint: disable=not-callable
        .select_from(NotificationReceipt)
        .where(
            NotificationReceipt.user_id == user_id,
            NotificationReceipt.event_type == TEST_EVENT,
            NotificationReceipt.occurred_at > now - 3600,
        )
    )
    if recent and recent >= 10:
        await db.rollback()
        raise EnrollmentError("Test notifications are limited to ten per hour", 429)
    identity = uuid4()
    db.add(
        NotificationReceipt(
            user_id=user_id,
            dedupe_key=f"provider-test:{identity}",
            event_type=TEST_EVENT,
            source="host.test",
            occurred_at=now,
        )
    )
    await db.commit()
    return identity


async def test_smtp(db: AsyncSession, user_id: UUID, address: str) -> dict:
    """Admin-authorized connection diagnostic to a single mailbox, using saved ENV settings."""
    address = normalize_email(address)
    configuration = await smtp_configuration(db)
    if not configuration.configured:
        raise EnrollmentError("Configure and save SMTP before sending a test")
    identity = await reserve_test(db, user_id)
    result = await deliver_mail(
        configuration,
        address,
        NotificationMessage(
            identity,
            "provider_test",
            TEST_TITLE,
            TEST_BODY,
            "system",
            identity,
            int(time.time()),
            purpose="test",
        ),
    )
    return {"sent": result.success, "error": result.error, "retryable": result.retryable}


async def queue_destination_test(
    db: AsyncSession,
    user_id: UUID,
    destination_id: UUID,
    *,
    variant: str = "basic",
    batch_size: int = 1,
) -> dict:
    """Tests use the same concrete endpoint, trust/preference checks and dispatcher as real work.

    The notification is bound to the one destination and carries the `test` purpose, so it can
    neither reach another endpoint nor impersonate a verification or recovery message. The
    variant selects representative canonical content; the real renderer applies the provider's
    capabilities to it.
    """
    destination = await db.scalar(
        select(NotificationDestination).where(
            NotificationDestination.id == destination_id,
            NotificationDestination.user_id == user_id,
            NotificationDestination.active.is_(True),
        )
    )
    if destination is None:
        raise EnrollmentError("Notification destination not found", 404)
    if variant not in VARIANTS or not 1 <= batch_size <= MAX_TEST_BATCH:
        raise EnrollmentError("Unsupported test variant or batch size", 422)
    preferences = await load_preferences(db, user_id)
    now = int(time.time())
    deliveries: list[dict] = []
    for position in range(batch_size):
        notice = _test_notice(user_id, destination_id, now, variant, position)
        if not select_projection(notice, destination, preferences):
            raise EnrollmentError("Enable this destination and its routing before sending a test")
        identity = await reserve_test(db, user_id)
        notice.id = identity
        notice.dedupe_key = f"provider-test:{identity}"
        db.add(notice)
        await db.flush()
        await ensure_deliveries(db, [notice.id], preferences=preferences)
        delivery = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice.id,
                NotificationDelivery.destination_id == destination_id,
            )
        )
        if delivery is None:
            await db.rollback()
            raise EnrollmentError("Enable this provider before sending a test")
        await db.commit()
        deliveries.append({"delivery_id": str(delivery.id), "status": delivery.status})
    return {
        **deliveries[0],
        "variant": variant,
        "queued": len(deliveries),
        "deliveries": deliveries,
    }


def _test_notice(
    user_id: UUID, destination_id: UUID, now: int, variant: str, position: int
) -> Notification:
    suffix = f" ({position + 1})" if position else ""
    return Notification(
        id=uuid4(),
        user_id=user_id,
        kind="provider_test",
        event_type=TEST_EVENT,
        source="host.test",
        media_type="notification_destination",
        media_id=destination_id,
        title=TEST_TITLE + suffix,
        body=TEST_BODY,
        group_key=f"variant:{variant}",
        event_at=now,
        required_trust=int(Trust.PUBLIC),
        purpose="test",
        inbox_visible=False,
        expires_at=now + 600,
    )
