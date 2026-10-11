"""Scoped, live host destination status for plugin configuration pages."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.features.notification_plugin_policy import delivery_projection, plugin_sensitive_allowed
from src.features.notification_policy import Trust, effective_trust
from src.features.notification_providers.plugin import PluginNotificationProvider
from src.plugin_api.notification_contracts import (
    NotificationDestinationStatusPage,
    NotificationDestinationStatusQuery,
    NotificationProviderDefinition,
)
from src.plugin_api.runtime_client import PluginRuntimeUnavailable


async def destination_status(
    db: AsyncSession, plugin_id: str, installation_id: UUID, user_id: UUID, payload: dict
) -> dict:
    """Only this actor's endpoints belonging to this installation's providers are visible."""
    query = NotificationDestinationStatusQuery.model_validate(payload)
    statement = (
        select(NotificationDestination, PluginNotificationProviderRegistration)
        .join(
            PluginNotificationProviderRegistration,
            (
                PluginNotificationProviderRegistration.provider_id
                == NotificationDestination.provider_id
            )
            & (PluginNotificationProviderRegistration.installation_id == installation_id),
        )
        .where(
            NotificationDestination.user_id == user_id,
            NotificationDestination.installation_id == installation_id,
            PluginNotificationProviderRegistration.plugin_id == plugin_id,
        )
        .order_by(NotificationDestination.id)
        .limit(query.limit + 1)
        .execution_options(populate_existing=True)
    )
    if query.provider_id is not None:
        statement = statement.where(NotificationDestination.provider_id == query.provider_id)
    if query.after is not None:
        statement = statement.where(NotificationDestination.id > query.after)
    rows = (await db.execute(statement)).all()
    settings = dict(
        (
            await db.execute(
                select(
                    NotificationProviderSetting.provider_id, NotificationProviderSetting.enabled
                ).where(NotificationProviderSetting.user_id == user_id)
            )
        )
        .tuples()
        .all()
    )
    preferences = await load_preferences(db, user_id)
    availability: dict[str, bool] = {}
    result = []
    for endpoint, registration in rows[: query.limit]:
        if registration.provider_id not in availability:
            try:
                availability[registration.provider_id] = await PluginNotificationProvider(
                    registration
                ).is_authorized(db, user_id) and (
                    not registration.definition
                    or not registration.definition.get("server_fields")
                    or registration.server_configured
                )
            except PluginRuntimeUnavailable:
                availability[registration.provider_id] = False
        available = availability[registration.provider_id]
        result.append(
            await _status_item(
                db,
                endpoint,
                registration,
                preferences,
                available=available,
                provider_enabled=settings.get(endpoint.provider_id, False),
            )
        )
    return NotificationDestinationStatusPage(
        installation_id=installation_id,
        destinations=result,
        next_after=rows[query.limit - 1][0].id if len(rows) > query.limit else None,
    ).model_dump(mode="json")


async def _status_item(db, endpoint, registration, preferences, *, available, provider_enabled):
    recovery = Notification(
        user_id=endpoint.user_id,
        kind="password_reset",
        event_type="auth.password_reset",
        required_trust=int(Trust.SECURE),
        purpose="recovery",
    )
    definition = (
        NotificationProviderDefinition.model_validate(registration.definition)
        if registration.definition
        else None
    )
    supported = bool(
        definition
        and any(
            choice.kind == endpoint.kind and choice.recovery_supported
            for choice in definition.destinations
        )
    )
    status = await _recovery_status(
        db,
        endpoint,
        recovery,
        preferences,
        supported=supported,
        available=available,
        sensitive=await plugin_sensitive_allowed(db, recovery, endpoint),
        provider_enabled=provider_enabled,
    )
    return {
        "id": endpoint.id,
        "provider_id": endpoint.provider_id,
        "kind": endpoint.kind,
        "revision": endpoint.revision,
        "trust": effective_trust(endpoint).name,
        "active": endpoint.active,
        "enabled": endpoint.enabled,
        "provider_available": available,
        "verification_available": available
        and await plugin_sensitive_allowed(db, Notification(purpose="verification"), endpoint),
        "recovery_allowed": endpoint.recovery_allowed,
        "recovery_status": status,
    }


# Explicit reasons make unavailable routing distinguishable from possession proof.
# pylint: disable-next=too-many-return-statements
async def _recovery_status(
    db, endpoint, recovery, preferences, *, supported, available, sensitive, provider_enabled
) -> str:
    if not supported:
        return "unsupported"
    if not available:
        return "provider_unavailable"
    if not endpoint.active or not endpoint.enabled or not provider_enabled:
        return "destination_disabled"
    if effective_trust(endpoint) != Trust.SECURE:
        return "verification_required"
    if not sensitive:
        return "sensitive_permission_required"
    if not endpoint.recovery_allowed:
        return "recovery_not_allowed"
    if not await delivery_projection(db, recovery, endpoint, preferences):
        return "routing_disabled"
    return "eligible"
