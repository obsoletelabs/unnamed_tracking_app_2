"""Adapter from durable plugin registrations to the core provider contract."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import decrypt_secret
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_notification_provider import (
    PluginNotificationProviderRegistration,
)
from src.database.models.user import User
from src.features.notification_urls import notification_url
from src.plugin_api.contracts import (
    NotificationDeliveryRepresentation,
    NotificationDeliveryResult,
    NotificationFieldLayout,
    NotificationProviderDefinition,
    PluginNotificationContent,
    PluginNotificationDestination,
)
from src.plugin_api.grants import has_capability_grant, installation_is_executable
from src.plugin_api.runtime_client import (
    PluginRuntimeClient,
    PluginRuntimeRequestError,
    PluginRuntimeUnavailable,
)

from .base import DeliveryResult, NotificationMessage, ProviderDestination
from .eligibility import endpoint_route_enabled, revalidate_delivery_attempt
from .webhook import discord_payload, normalize_discord_webhook


class PluginNotificationProvider:
    """Core-controlled delivery adapter for one registered plugin provider."""

    def __init__(
        self,
        registration: PluginNotificationProviderRegistration,
        runtime: PluginRuntimeClient | None = None,
    ) -> None:
        self.registration = registration
        self.id = registration.provider_id
        self.name = registration.name
        self.transport = getattr(registration, "transport", None) or "legacy"
        self._runtime = runtime or PluginRuntimeClient()
        definition = getattr(registration, "definition", None)
        self.definition = (
            NotificationProviderDefinition.model_validate(definition) if definition else None
        )
        self.critical_supported = bool(
            self.definition and self.definition.features.critical_supported
        )

    async def lookup_destination(
        self,
        db: AsyncSession,
        user: User,
        setting: NotificationProviderSetting | None,
    ) -> ProviderDestination | None:
        if setting is None or not setting.enabled or setting.user_id != user.id:
            return None
        if self.transport != "legacy":
            return None
        allowed = await self.is_authorized(db, user.id)
        if not allowed:
            return None
        return ProviderDestination(user_id=user.id, display=self.name)

    async def lookup_endpoint(
        self,
        db: AsyncSession,
        user: User,
        setting: NotificationProviderSetting | None,
        endpoint: NotificationDestination,
    ) -> ProviderDestination | None:
        if (
            not endpoint_route_enabled(self.id, user.id, setting, endpoint)
            or endpoint.installation_id != self.registration.installation_id
        ):
            return None
        if not await self.is_authorized(db, user.id):
            return None
        if self.transport == "plugin":
            return (
                ProviderDestination.for_endpoint(endpoint, self.name)
                if self._generic_endpoint_valid(endpoint)
                else None
            )
        protected = self.transport == "discord_webhook"
        if protected and (
            endpoint.kind != "discord_webhook" or not endpoint.encrypted_configuration
        ):
            return None
        if not protected and endpoint.kind != "legacy_webhook":
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
                PluginNotificationProviderRegistration.definition == self.registration.definition
                if self.transport == "plugin"
                else PluginNotificationProviderRegistration.definition.is_(None),
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
            if self.transport == "plugin":
                return await self._deliver_generic(db, destination, message)
            if self.transport == "discord_webhook":
                return await self._deliver_protected(db, destination, message, work)
            response = await self._runtime.notification_delivery(
                self.registration.plugin_id,
                self.registration.action_id,
                {
                    "delivery": work.model_dump(mode="json"),
                },
                user_id=str(destination.user_id),
                installation_id=str(self.registration.installation_id),
                attempt_id=str(message.attempt_id),
            )
            result = NotificationDeliveryResult.model_validate(response)
        except (PluginRuntimeRequestError, PluginRuntimeUnavailable, ValueError) as exc:
            return DeliveryResult(success=False, retryable=True, error=str(exc)[:512])
        return DeliveryResult(
            success=result.success,
            retryable=result.retryable,
            error=result.error,
        )

    def _generic_endpoint_valid(self, endpoint: NotificationDestination) -> bool:
        """A provider kind cannot promote a destination or claim a host secret."""
        if not self.definition or endpoint.installation_id != self.registration.installation_id:
            return False
        choice = next(
            (item for item in self.definition.destinations if item.kind == endpoint.kind), None
        )
        return bool(
            choice
            and endpoint.privacy == (1 if choice.privacy == "PRIVATE" else 0)
            and endpoint.channel_context == "external"
            and not endpoint.encrypted_configuration
            and endpoint.configuration_ref == str(endpoint.id)
        )

    async def _deliver_generic(self, db, destination, message) -> DeliveryResult:
        """Revalidate durable authority immediately before disclosing approved content."""
        endpoint = await revalidate_delivery_attempt(db, destination, message)
        if endpoint is None or not self._generic_endpoint_valid(endpoint):
            return DeliveryResult(False, error="provider_routing_changed")
        if message.purpose not in {"standard", "test"}:
            return DeliveryResult(False, error="provider_secure_proof_required")
        link = await notification_url(db, endpoint.user_id, endpoint)
        content = PluginNotificationContent(
            notification_id=message.id,
            event_type=message.event_type or message.kind,
            title=message.title,
            body=message.body,
            event_at=message.event_at,
            urgency=message.urgency if self.critical_supported else "normal",
            link=link,
        )
        endpoint = await revalidate_delivery_attempt(db, destination, message)
        if (
            endpoint is None
            or not self._generic_endpoint_valid(endpoint)
            or not await self.is_authorized(db, destination.user_id)
        ):
            return DeliveryResult(False, error="provider_routing_changed")
        reference = PluginNotificationDestination(
            id=endpoint.id, revision=endpoint.revision, kind=endpoint.kind
        )
        try:
            response = await self._runtime.notification_delivery(
                self.registration.plugin_id,
                self.registration.action_id,
                {
                    "delivery": content.model_dump(mode="json"),
                    "destination": reference.model_dump(mode="json"),
                },
                user_id=str(destination.user_id),
                installation_id=str(self.registration.installation_id),
                attempt_id=str(message.attempt_id),
            )
            result = NotificationDeliveryResult.model_validate(response)
        except (PluginRuntimeRequestError, PluginRuntimeUnavailable):
            return DeliveryResult(False, retryable=True, error="provider_transport_unavailable")
        except ValueError:
            return DeliveryResult(False, error="provider_result_invalid")
        # Arbitrary plugin error strings can contain its secrets; core stores only safe codes.
        return DeliveryResult(
            result.success, result.retryable, None if result.success else "provider_delivery_failed"
        )

    # Distinct fail-closed outcomes keep each routing and transport gate visible.
    # pylint: disable-next=too-many-return-statements
    async def _deliver_protected(self, db, destination, message, work) -> DeliveryResult:
        try:
            plan = await self._runtime.notification_layout(
                self.registration.plugin_id,
                self.registration.action_id,
                {"delivery": work.model_dump(mode="json")},
                user_id=str(destination.user_id),
                installation_id=str(self.registration.installation_id),
                attempt_id=str(message.attempt_id),
            )
            layout = NotificationFieldLayout.model_validate(plan)
        except (PluginRuntimeRequestError, PluginRuntimeUnavailable):
            return DeliveryResult(False, retryable=True, error="provider_renderer_unavailable")
        except ValueError:
            return DeliveryResult(False, error="provider_layout_invalid")
        endpoint = await revalidate_delivery_attempt(db, destination, message)
        if endpoint is None or not await self.is_authorized(db, destination.user_id):
            return DeliveryResult(False, error="provider_routing_changed")
        if (
            endpoint.installation_id != self.registration.installation_id
            or not endpoint.encrypted_configuration
        ):
            return DeliveryResult(False, error="provider_endpoint_changed")
        link = await notification_url(db, endpoint.user_id, endpoint)
        payload = discord_payload(layout, message, link)
        endpoint = await revalidate_delivery_attempt(db, destination, message)
        if endpoint is None or not endpoint.encrypted_configuration:
            return DeliveryResult(False, error="provider_routing_changed")
        try:
            url = normalize_discord_webhook(
                json.loads(decrypt_secret(endpoint.encrypted_configuration))["webhook"]
            )
            response = await self._runtime.notification_transport(
                self.registration.plugin_id,
                url,
                payload,
                user_id=str(endpoint.user_id),
                installation_id=str(self.registration.installation_id),
                attempt_id=str(message.attempt_id),
            )
            result = NotificationDeliveryResult.model_validate(response)
        except (PluginRuntimeRequestError, PluginRuntimeUnavailable):
            return DeliveryResult(False, retryable=True, error="provider_transport_unavailable")
        except (ValueError, KeyError):
            return DeliveryResult(False, error="provider_configuration_invalid")
        return DeliveryResult(result.success, result.retryable, result.error)
