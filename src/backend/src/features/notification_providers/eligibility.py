"""Core eligibility gate shared by delivery coordination and protected transports."""

import time
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_delivery_attempt import NotificationDeliveryAttempt
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.features.notification_policy import select_projection
from src.features.notification_source_policy import source_delivery_allowed

from .base import NotificationMessage, ProviderDestination


def endpoint_route_enabled(
    provider_id: str,
    user_id: UUID,
    setting: NotificationProviderSetting | None,
    endpoint: NotificationDestination,
) -> bool:
    """Require matching owner/provider identity as well as active endpoint opt-in."""
    return bool(
        setting
        and setting.enabled
        and setting.user_id == user_id
        and setting.provider_id == provider_id
        and endpoint.user_id == user_id
        and endpoint.provider_id == provider_id
        and endpoint.active
        and endpoint.enabled
    )


async def revalidate_delivery_attempt(
    db: AsyncSession, destination: ProviderDestination, message: NotificationMessage
) -> NotificationDestination | None:
    """Recheck core state after plugin rendering, immediately before protected transport."""
    work = (
        await db.execute(
            select(NotificationDelivery, Notification, NotificationDestination)
            .join(
                NotificationDeliveryAttempt,
                NotificationDeliveryAttempt.delivery_id == NotificationDelivery.id,
            )
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .join(
                NotificationDestination,
                NotificationDestination.id == NotificationDelivery.destination_id,
            )
            .where(
                NotificationDeliveryAttempt.id == message.attempt_id,
                NotificationDeliveryAttempt.claim_token == NotificationDelivery.claim_token,
                NotificationDelivery.status == "processing",
                NotificationDelivery.lease_until > int(time.time()),
                Notification.id == message.id,
                NotificationDestination.id == destination.endpoint_id,
                NotificationDestination.user_id == destination.user_id,
                NotificationDestination.revision == destination.endpoint_revision,
            )
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if work is None:
        return None
    delivery, notification, endpoint = work
    if (
        notification.deleted_at is not None
        or (notification.expires_at is not None and notification.expires_at <= int(time.time()))
        or not await source_delivery_allowed(db, notification)
    ):
        return None
    preferences = await load_preferences(db, destination.user_id)
    if select_projection(notification, endpoint, preferences) != delivery.projection:
        return None
    setting = await db.scalar(
        select(NotificationProviderSetting).where(
            NotificationProviderSetting.user_id == destination.user_id,
            NotificationProviderSetting.provider_id == endpoint.provider_id,
            NotificationProviderSetting.enabled.is_(True),
        )
    )
    return endpoint if setting else None
