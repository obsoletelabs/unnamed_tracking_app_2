"""Durable source authorization, separate from notification interpretation and transport."""

import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.plugin_notification_type import PluginNotificationTypeRegistration
from src.features.notification_audit import change_deliveries
from src.features.notification_policy import Trust
from src.plugin_api.grants import has_capability_grant


async def source_delivery_allowed(db: AsyncSession, notice: Notification) -> bool:
    """Revocation/reinstall cannot release old source work through a built-in provider."""
    if notice.source_installation_id is None:
        return True
    legacy = notice.kind == "plugin" and notice.event_type == "plugin.notice"
    if not legacy:
        row = await db.scalar(
            select(PluginNotificationTypeRegistration).where(
                PluginNotificationTypeRegistration.plugin_id == notice.source,
                PluginNotificationTypeRegistration.installation_id == notice.source_installation_id,
                PluginNotificationTypeRegistration.event_type == notice.event_type,
                PluginNotificationTypeRegistration.revoked_at.is_(None),
            )
        )
        if row is None:
            return False
    capabilities = ["notifications.send" if legacy else "notifications.emit"]
    if notice.required_trust == Trust.SECURE:
        capabilities.append("notifications.sensitive")
    for capability in capabilities:
        if not await has_capability_grant(
            db,
            plugin_id=notice.source,
            installation_id=notice.source_installation_id,
            user_id=notice.user_id,
            capability=capability,
        ):
            return False
    return True


async def retire_sources(
    db: AsyncSession, plugin_id: str, *, event_type: str | None = None
) -> None:
    """Preserve type/history/preferences but close all unsent work before reactivation."""
    types = select(PluginNotificationTypeRegistration.event_type).where(
        PluginNotificationTypeRegistration.plugin_id == plugin_id
    )
    if event_type is not None:
        types = types.where(PluginNotificationTypeRegistration.event_type == event_type)
    await db.execute(
        update(PluginNotificationTypeRegistration)
        .where(PluginNotificationTypeRegistration.event_type.in_(types))
        .values(revoked_at=int(time.time()))
    )
    notices = select(Notification.id).where(
        Notification.source == plugin_id, Notification.source_installation_id.is_not(None)
    )
    if event_type is not None:
        notices = notices.where(Notification.event_type == event_type)
    await change_deliveries(
        db,
        update(NotificationDelivery)
        .where(
            NotificationDelivery.notification_id.in_(notices),
            NotificationDelivery.status.in_(("pending", "processing", "retry_wait")),
        )
        .values(
            status="suppressed", last_error="source_revoked", claim_token=None, lease_until=None
        ),
    )
