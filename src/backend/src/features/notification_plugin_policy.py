"""Explicit high-risk handoff authority, separate from source grants and destination proof."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.features.notification_pending import pending_projection
from src.features.notification_policy import Trust, select_projection
from src.plugin_api.grants import has_capability_grant
from src.plugin_api.notification_contracts import NotificationProviderDefinition


async def plugin_sensitive_allowed(
    db: AsyncSession, notification: Notification, destination: NotificationDestination
) -> bool:
    """Declarations never grant access; legacy and grouped endpoints cannot gain proof."""
    concrete_private_endpoint = (
        destination.user_id is not None,
        destination.pending_recipient_id is None,
        destination.installation_id is not None,
        destination.privacy == Trust.PRIVATE,
        destination.channel_context == "external",
        destination.configuration_ref == str(destination.id),
        destination.endpoint_key == str(destination.id),
        not destination.encrypted_configuration,
    )
    if not all(concrete_private_endpoint):
        return False
    registration = await db.scalar(
        select(PluginNotificationProviderRegistration)
        .where(
            PluginNotificationProviderRegistration.provider_id == destination.provider_id,
            PluginNotificationProviderRegistration.installation_id == destination.installation_id,
            PluginNotificationProviderRegistration.revoked_at.is_(None),
            PluginNotificationProviderRegistration.transport == "plugin",
        )
        .execution_options(populate_existing=True)
    )
    if registration is None or not registration.definition:
        return False
    definition = NotificationProviderDefinition.model_validate(registration.definition)
    choice = next((item for item in definition.destinations if item.kind == destination.kind), None)
    if (
        choice is None
        or choice.privacy != "PRIVATE"
        or definition.server_fields
        and not registration.server_configured
    ):
        return False
    if notification.purpose == "verification":
        supported = choice.verification_supported
    else:
        supported = choice.sensitive_supported and (
            notification.purpose != "recovery" or choice.recovery_supported
        )
        supported = supported and destination.verification_method == "plugin.code.v1"
    if not supported:
        return False
    for capability in ("notification_providers.deliver", "notification_providers.sensitive"):
        if not await has_capability_grant(
            db,
            plugin_id=registration.plugin_id,
            installation_id=registration.installation_id,
            capability=capability,
            user_id=destination.user_id,
        ):
            return False
    return True


async def delivery_projection(db, notification, destination, preferences) -> str | None:
    """One routing policy used when work is created and at every dispatch recheck."""
    if notification.pending_recipient_id is not None:
        return await pending_projection(db, notification, destination)
    sensitive = destination.installation_id is not None and (
        max(notification.required_trust, settings.NOTIFICATION_MINIMUM_TRUST) == Trust.SECURE
        or notification.purpose == "verification"
    )
    allowed = await plugin_sensitive_allowed(db, notification, destination) if sensitive else False
    return select_projection(
        notification, destination, preferences, plugin_sensitive_allowed=allowed
    )
