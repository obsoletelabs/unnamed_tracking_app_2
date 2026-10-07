"""Discover native core providers and permission-checked optional plugins."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.plugin_metadata_provider import (
    PluginMetadataProviderRegistration,
)
from src.database.session import SessionLocal
from src.plugin_api.grants import has_capability_grant, installation_is_executable
from src.plugin_api.metadata_contracts import (
    MediaType,
    MetadataCandidate,
    MetadataProviderRegistration,
    MetadataProviderRequest,
    ProviderFailure,
    ProviderHealth,
    ProviderResponse,
)
from src.plugin_api.runtime_client import (
    PluginRuntimeClient,
    PluginRuntimeRequestError,
    PluginRuntimeUnavailable,
)

from .configuration import configuration_presence, configuration_values, save_configuration
from .core import CORE_PROVIDERS, CoreMetadataProvider, discover_core_providers
from .deadlines import bounded

logger = logging.getLogger(__name__)
Operation = Literal["search", "metadata", "media", "health"]


async def register_provider(
    db: AsyncSession, plugin_id: str, installation_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Bind an idempotent, validated registration to the caller's installation."""
    declaration = MetadataProviderRegistration.model_validate(payload)
    if not declaration.provider_id.startswith(plugin_id + "."):
        raise PermissionError("metadata provider ID must belong to this plugin")
    document = await bounded(PluginRuntimeClient().plugin_ui(plugin_id), 3)
    actions = {action["id"]: action for action in document.get("actions", [])}
    for operation, action_id in declaration.operations.model_dump().items():
        if action_id is None:
            continue
        action = actions.get(action_id)
        if not action or action.get("capability", {}).get("name") != (
            "metadata_providers." + operation
        ):
            raise ValueError("provider operation must declare its matching action capability")
    existing = await db.get(PluginMetadataProviderRegistration, declaration.provider_id)
    if existing is not None and existing.plugin_id != plugin_id:
        raise PermissionError("metadata provider identity belongs to another plugin")
    if existing is None:
        existing = PluginMetadataProviderRegistration(provider_id=declaration.provider_id)
        db.add(existing)
    existing.plugin_id = plugin_id
    existing.installation_id = installation_id
    existing.declaration = declaration.model_dump(mode="json")
    existing.registered_at = int(time.time())
    existing.revoked_at = None
    await db.commit()
    return {"registered": True, "provider_id": declaration.provider_id}


async def unregister_provider(
    db: AsyncSession, plugin_id: str, installation_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Revoke only this installation's own registration."""
    row = await db.get(PluginMetadataProviderRegistration, payload.get("provider_id"))
    if row is None:
        raise LookupError("metadata provider registration not found")
    if row.plugin_id != plugin_id or row.installation_id != installation_id:
        raise PermissionError("metadata provider registration belongs to another installation")
    row.revoked_at = int(time.time())
    await db.commit()
    return {"unregistered": True, "provider_id": row.provider_id}


async def provider_configuration(
    db: AsyncSession,
    plugin_id: str,
    installation_id: UUID,
    user_id: UUID,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Return credentials only through an authenticated, installation-bound plugin call."""
    row = await db.get(PluginMetadataProviderRegistration, payload.get("provider_id"))
    if row is None or row.revoked_at is not None:
        raise LookupError("metadata provider registration not found")
    if row.plugin_id != plugin_id or row.installation_id != installation_id:
        raise PermissionError("metadata provider configuration belongs to another installation")
    values, revision = await configuration_values(db, row, user_id)
    return {"values": values, "revision": revision}


@dataclass(frozen=True)
class PluginMetadataProvider:
    """A discovery snapshot; every operation rechecks current lifecycle and permissions."""

    declaration: MetadataProviderRegistration
    plugin_id: str
    installation_id: UUID
    user_id: UUID
    revision: str
    state: ProviderHealth
    priority: int = 100

    @property
    def id(self) -> str:
        return self.declaration.provider_id

    @property
    def name(self) -> str:
        return self.declaration.name

    async def authorized(self, operation: Operation) -> bool:
        async with SessionLocal() as db:
            registration = await db.get(PluginMetadataProviderRegistration, self.id)
            if (
                registration is None
                or registration.revoked_at is not None
                or registration.installation_id != self.installation_id
            ):
                return False
            for capability in ("metadata_providers.register", "metadata_providers." + operation):
                if not await has_capability_grant(
                    db,
                    plugin_id=self.plugin_id,
                    installation_id=self.installation_id,
                    capability=capability,
                    user_id=self.user_id,
                ):
                    return False
            _, revision = await configuration_values(db, registration, self.user_id)
            if revision != self.revision:
                return False
        plugin = await bounded(PluginRuntimeClient().plugin_state(self.plugin_id), 2)
        return (
            plugin.get("plugin_id") == self.plugin_id
            and plugin.get("installation_id") == str(self.installation_id)
            and installation_is_executable(plugin)
        )

    async def invoke(
        self,
        operation: Operation,
        request: MetadataProviderRequest,
        candidate: MetadataCandidate | None = None,
    ) -> ProviderResponse:
        """No provider-specific API, credentials, retries or throttling live in this adapter."""
        action_id = getattr(self.declaration.operations, operation)
        if not action_id or not await self.authorized(operation):
            return ProviderResponse(failure=ProviderFailure(code="plugin_unavailable"))
        values = {"request": request.model_dump(mode="json"), "provider_id": self.id}
        if candidate is not None:
            values["candidate"] = candidate.model_dump(mode="json")
        try:
            payload = await PluginRuntimeClient().action(
                self.plugin_id, action_id, values, user_id=str(self.user_id)
            )
            result = ProviderResponse.model_validate(payload)
        except PluginRuntimeUnavailable:
            return ProviderResponse(failure=ProviderFailure(code="plugin_unavailable"))
        except ValidationError as exc:
            logger.warning(
                "metadata_invalid_response provider=%s operation=%s fields=%s",
                self.id,
                operation,
                [(error["loc"], error["type"]) for error in exc.errors()],
            )
            return ProviderResponse(failure=ProviderFailure(code="invalid_response"))
        except (PluginRuntimeRequestError, ValueError):
            return ProviderResponse(failure=ProviderFailure(code="invalid_response"))
        if not await self.authorized(operation):
            return ProviderResponse(failure=ProviderFailure(code="plugin_unavailable"))
        return result


def _provider_state(
    declaration: MetadataProviderRegistration,
    values: dict[str, str],
    installation_id: UUID,
    plugin: dict,
) -> ProviderHealth:
    if plugin.get("installation_id") != str(installation_id):
        return ProviderHealth.PLUGIN_UNAVAILABLE
    if not plugin.get("enabled"):
        return ProviderHealth.DISABLED
    if not installation_is_executable(plugin):
        return ProviderHealth.PLUGIN_UNAVAILABLE
    if any(field.required and not values.get(field.key) for field in declaration.configuration):
        return ProviderHealth.INVALID_CONFIGURATION if values else ProviderHealth.NOT_CONFIGURED
    return ProviderHealth.UNVALIDATED


async def discover_providers(
    db: AsyncSession,
    user_id: UUID,
    media_type: MediaType | None = None,
    preferences: dict[str, Any] | None = None,
) -> list[PluginMetadataProvider | CoreMetadataProvider]:
    """Derive discovery from durable registrations, current lifecycle and scoped grants."""
    providers: list[PluginMetadataProvider | CoreMetadataProvider] = list(
        await discover_core_providers(db, user_id, media_type, preferences)
    )
    registrations = list(
        await db.scalars(
            select(PluginMetadataProviderRegistration)
            .where(PluginMetadataProviderRegistration.revoked_at.is_(None))
            .order_by(PluginMetadataProviderRegistration.provider_id)
        )
    )
    registrations = [row for row in registrations if row.provider_id not in CORE_PROVIDERS]
    for row in list(registrations):
        if await has_capability_grant(
            db,
            plugin_id=row.plugin_id,
            installation_id=row.installation_id,
            capability="metadata_providers.register",
            user_id=user_id,
        ):
            continue
        registrations.remove(row)
    if not registrations:
        return providers
    try:
        plugins = await bounded(PluginRuntimeClient().plugins(), 2)
    except (PluginRuntimeUnavailable, PluginRuntimeRequestError, TimeoutError):
        plugins = []
    live = {plugin["plugin_id"]: plugin for plugin in plugins}
    for row in registrations:
        declaration = MetadataProviderRegistration.model_validate(row.declaration)
        if media_type is not None and media_type not in declaration.media_types:
            continue
        values, revision = await configuration_values(db, row, user_id)
        state = _provider_state(
            declaration, values, row.installation_id, live.get(row.plugin_id, {})
        )
        order = (preferences or {}).get("provider_order", [])
        priority = order.index(declaration.name) if declaration.name in order else 100
        providers.append(
            PluginMetadataProvider(
                declaration, row.plugin_id, row.installation_id, user_id, revision, state, priority
            )
        )
    return providers


__all__ = [
    "configuration_values",
    "configuration_presence",
    "save_configuration",
    "discover_providers",
    "register_provider",
    "unregister_provider",
    "provider_configuration",
    "PluginMetadataProvider",
]
