"""Adapter from durable plugin registrations to the core provider contract."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.models.user import User
from src.features.notification_policy import Trust
from src.plugin_api.contracts import NotificationDeliveryRepresentation, NotificationDeliveryResult
from src.plugin_api.grants import has_capability_grant, installation_is_executable
from src.plugin_api.runtime_client import (
    PluginRuntimeClient,
    PluginRuntimeRequestError,
    PluginRuntimeUnavailable,
)

from .base import DeliveryResult, NotificationMessage, ProviderDestination
from .eligibility import endpoint_route_enabled, revalidate_delivery_attempt


class PluginNotificationProvider:
    """Core routing adapter; plugin code owns settings and actual delivery."""

    def __init__(
        self,
        registration: PluginNotificationProviderRegistration,
        runtime: PluginRuntimeClient | None = None,
    ) -> None:
        self.registration = registration
        self.id = registration.provider_id
        self.name = registration.name
        self.transport = registration.transport
        self._runtime = runtime or PluginRuntimeClient()

    @property
    def is_plugin_owned(self) -> bool:
        return self.transport in {"plugin_public", "plugin_private"}

    async def lookup_destination(
        self,
        db: AsyncSession,
        user: User,
        setting: NotificationProviderSetting | None,
    ) -> ProviderDestination | None:
        if setting is None or not setting.enabled or setting.user_id != user.id:
            return None
        if self.is_plugin_owned or self.transport != "legacy":
            return None
        if not await self.is_authorized(db, user.id):
            return None
        return ProviderDestination(user_id=user.id, display=self.name)

    async def lookup_endpoint(
        self,
        db: AsyncSession,
        user: User,
        setting: NotificationProviderSetting | None,
        endpoint: NotificationDestination,
    ) -> ProviderDestination | None:
        if not endpoint_route_enabled(self.id, user.id, setting, endpoint):
            return None
        if endpoint.installation_id != self.registration.installation_id:
            return None
        if not await self.is_authorized(db, user.id):
            return None
        if self.is_plugin_owned:
            expected_privacy = (
                int(Trust.PRIVATE) if self.transport == "plugin_private" else int(Trust.PUBLIC)
            )
            if endpoint.kind != self.registration.destination_kind:
                return None
            if endpoint.privacy != expected_privacy or endpoint.encrypted_configuration:
                return None
            return ProviderDestination.for_endpoint(endpoint, self.name)
        if endpoint.kind != "legacy_webhook":
            return None
        return ProviderDestination.for_endpoint(endpoint, self.name)

    async def is_authorized(self, db: AsyncSession, user_id: UUID) -> bool:
        """Recheck durable registration, grants and live installation at delivery."""
        allowed = await has_capability_grant(
            db,
            plugin_id=self.registration.plugin_id,
            installation_id=self.registration.installation_id,
            capability="notification_providers.deliver",
            user_id=user_id,
        )
        if not allowed:
            return False
        active_user = await db.scalar(
            select(User.id).where(User.id == user_id, User.is_active.is_(True))
        )
        if active_user is None:
            return False
        registration = await db.scalar(
            select(PluginNotificationProviderRegistration.id).where(
                PluginNotificationProviderRegistration.id == self.registration.id,
                PluginNotificationProviderRegistration.plugin_id == self.registration.plugin_id,
                PluginNotificationProviderRegistration.installation_id
                == self.registration.installation_id,
                PluginNotificationProviderRegistration.revoked_at.is_(None),
                PluginNotificationProviderRegistration.action_id == self.registration.action_id,
                PluginNotificationProviderRegistration.transport == self.transport,
            )
        )
        if registration is None:
            return False
        installed = await self._runtime.plugins()
        matches = [
            item for item in installed if item.get("plugin_id") == self.registration.plugin_id
        ]
        return (
            len(matches) == 1
            and matches[0].get("installation_id") == str(self.registration.installation_id)
            and installation_is_executable(matches[0])
        )

    async def deliver(
        self,
        db: AsyncSession,
        destination: ProviderDestination,
        message: NotificationMessage,
    ) -> DeliveryResult:
        work = NotificationDeliveryRepresentation(
            notification_id=message.id,
            kind=message.kind,
            title=message.title,
            body=message.body,
            media_type=message.media_type,
            media_id=message.media_id,
            event_at=message.event_at,
        )
        try:
            if not await self.is_authorized(db, destination.user_id):
                return DeliveryResult(
                    success=False, error="Plugin provider authorization is unavailable."
                )
            if message.attempt_id is None:
                return DeliveryResult(success=False, error="Core delivery attempt is required.")
            endpoint = None
            if destination.endpoint_id is not None:
                endpoint = await revalidate_delivery_attempt(db, destination, message)
                if endpoint is None:
                    return DeliveryResult(False, error="provider_routing_changed")
            response = await self._runtime.notification_delivery(
                self.registration.plugin_id,
                self.registration.action_id,
                {
                    "delivery": work.model_dump(mode="json"),
                    "destination": {
                        "id": str(endpoint.id) if endpoint is not None else None,
                        "endpoint_key": endpoint.endpoint_key if endpoint is not None else None,
                        "kind": endpoint.kind
                        if endpoint is not None
                        else self.registration.destination_kind,
                        "configuration_ref": endpoint.configuration_ref
                        if endpoint is not None
                        else None,
                        "privacy": endpoint.privacy if endpoint is not None else int(Trust.PUBLIC),
                    },
                    "_plugin_context": {"user_id": str(destination.user_id), "is_admin": False},
                },
                user_id=str(destination.user_id),
                installation_id=str(self.registration.installation_id),
                attempt_id=str(message.attempt_id),
            )
            result = NotificationDeliveryResult.model_validate(response)
        except (PluginRuntimeRequestError, PluginRuntimeUnavailable, ValueError) as exc:
            return DeliveryResult(success=False, retryable=True, error=str(exc)[:512])
        return DeliveryResult(
            success=result.success, retryable=result.retryable, error=result.error
        )
