"""Hardcoded core providers, independent of plugin installation and runtime."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import encrypt_secret
from src.core.provider_credentials import resolve_metadata_provider_credentials
from src.database.models.app_integration_settings import AppIntegrationSettings
from src.database.models.plugin_metadata_provider import (
    PluginMetadataProviderRegistration,
    PluginProviderConfiguration,
)
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.metadata import core_http
from src.features.metadata.builtin import anilist, anizip, igdb, omdb, steam, steamgriddb, tvmaze
from src.plugin_api.metadata_contracts import (
    MediaType,
    MetadataCandidate,
    MetadataProviderRegistration,
    MetadataProviderRequest,
    ProviderHealth,
    ProviderResponse,
)

from .configuration import configuration_values

CORE_PROVIDERS: dict[str, ModuleType] = {
    module.PROVIDER_ID: module
    for module in (steam, igdb, steamgriddb, tvmaze, anilist, anizip, omdb)
}
_LEGACY_FIELDS = {
    "core.steamgriddb": {"api_key": "steamgriddb_api_key"},
    "core.igdb": {"client_id": "igdb_client_id", "client_secret": "igdb_client_secret"},
    "core.omdb": {"api_key": "omdb_api_key"},
}


async def core_registration(
    db: AsyncSession, provider_id: str
) -> PluginMetadataProviderRegistration:
    """Persist only the host-owned declaration used by the shared credential store."""
    module = CORE_PROVIDERS[provider_id]
    declaration = MetadataProviderRegistration.model_validate(module.DECLARATION)
    row = await db.get(PluginMetadataProviderRegistration, provider_id)
    if row is None:
        await db.execute(
            insert(PluginMetadataProviderRegistration)
            .values(
                provider_id=provider_id,
                plugin_id="host.metadata",
                installation_id=uuid5(NAMESPACE_URL, "host-metadata/" + provider_id),
                declaration=declaration.model_dump(mode="json"),
                registered_at=int(time.time()),
            )
            .on_conflict_do_nothing(index_elements=["provider_id"])
        )
        await db.commit()
        row = await db.get(PluginMetadataProviderRegistration, provider_id)
        assert row is not None
    if (
        row.plugin_id == "host.metadata"
        and row.declaration == declaration.model_dump(mode="json")
        and row.revoked_at is None
    ):
        return row
    row.plugin_id = "host.metadata"
    row.installation_id = uuid5(NAMESPACE_URL, "host-metadata/" + provider_id)
    row.declaration = declaration.model_dump(mode="json")
    row.registered_at = int(time.time())
    row.revoked_at = None
    await db.commit()
    return row


async def migrate_core_credentials(
    db: AsyncSession, row: PluginMetadataProviderRegistration, user_id: UUID
) -> None:
    """Carry forward old keys once; an existing or cleared new scope always wins."""
    fields = _LEGACY_FIELDS.get(row.provider_id)
    if fields is None:
        return
    integrations = await db.scalar(select(AppIntegrationSettings).limit(1))
    scopes = {"system": resolve_metadata_provider_credentials(None, integrations)}
    user = await db.get(User, user_id)
    if row.provider_id == "core.steamgriddb" and user and user.steamgriddb_api_key:
        scopes[str(user_id)] = resolve_metadata_provider_credentials(user)
    for scope, credentials in scopes.items():
        existing = await db.scalar(
            select(PluginProviderConfiguration).where(
                PluginProviderConfiguration.provider_id == row.provider_id,
                PluginProviderConfiguration.scope == scope,
            )
        )
        values = {
            key: value
            for key, attribute in fields.items()
            if (value := getattr(credentials, attribute))
        }
        if existing is None and values:
            await db.execute(
                insert(PluginProviderConfiguration)
                .values(
                    provider_id=row.provider_id,
                    plugin_id="host.metadata",
                    scope=scope,
                    encrypted_values=encrypt_secret(json.dumps(values)),
                    updated_at=int(time.time()),
                )
                .on_conflict_do_nothing(index_elements=["provider_id", "scope"])
            )
    await db.commit()


@dataclass(frozen=True)
class CoreMetadataProvider:
    """Native operation adapter for the same progressive handler used by plugins."""

    declaration: MetadataProviderRegistration
    user_id: UUID
    revision: str
    state: ProviderHealth
    values: dict[str, str] = field(repr=False)
    priority: int = 100
    plugin_id: str = "host.metadata"  # Shared storage owner; there is no installed plugin.

    @property
    def id(self) -> str:
        return self.declaration.provider_id

    @property
    def name(self) -> str:
        return self.declaration.name

    @property
    def installation_id(self) -> UUID:
        return uuid5(NAMESPACE_URL, "host-metadata/" + self.id)

    async def authorized(self, _operation: str) -> bool:
        """A cancelled or cached operation cannot reuse a replaced credential context."""
        async with SessionLocal() as db:
            row = await db.get(PluginMetadataProviderRegistration, self.id)
            if row is None:
                return False
            _, revision = await configuration_values(db, row, self.user_id)
            return revision == self.revision

    async def invoke(
        self,
        operation: str,
        request: MetadataProviderRequest,
        candidate: MetadataCandidate | None = None,
    ) -> ProviderResponse:
        if request.user_id != self.user_id:
            return ProviderResponse.model_validate({"failure": {"code": "invalid_configuration"}})
        if not getattr(self.declaration.operations, operation):
            return ProviderResponse()
        if not await self.authorized(operation):
            return ProviderResponse.model_validate({"failure": {"code": "invalid_configuration"}})
        work = request.model_dump(mode="json")
        values = {"request": work}
        if candidate is not None:
            values["candidate"] = candidate.model_dump(mode="json")
        token = core_http.context.set((work, self.values))
        try:
            result = getattr(CORE_PROVIDERS[self.id], operation)(values)
            if inspect.isawaitable(result):
                result = await result
            response = ProviderResponse.model_validate(result)
        except core_http.ProviderFailure as exc:
            response = ProviderResponse.model_validate(exc.response())
        except TimeoutError:
            response = ProviderResponse.model_validate({"failure": {"code": "timeout"}})
        except (ValueError, KeyError, TypeError, ValidationError):
            response = ProviderResponse.model_validate({"failure": {"code": "invalid_response"}})
        finally:
            core_http.context.reset(token)
        if not await self.authorized(operation):
            return ProviderResponse.model_validate({"failure": {"code": "invalid_configuration"}})
        return response


async def discover_core_providers(
    db: AsyncSession,
    user_id: UUID,
    media_type: MediaType | None = None,
    preferences: dict[str, Any] | None = None,
) -> list[CoreMetadataProvider]:
    providers = []
    for provider_id, module in CORE_PROVIDERS.items():
        declaration = MetadataProviderRegistration.model_validate(module.DECLARATION)
        if media_type is not None and media_type not in declaration.media_types:
            continue
        row = await core_registration(db, provider_id)
        await migrate_core_credentials(db, row, user_id)
        values, revision = await configuration_values(db, row, user_id)
        missing = any(
            field.required and not values.get(field.key) for field in declaration.configuration
        )
        state = (
            (ProviderHealth.INVALID_CONFIGURATION if values else ProviderHealth.NOT_CONFIGURED)
            if missing
            else ProviderHealth.UNVALIDATED
        )
        order = (preferences or {}).get("provider_order", [])
        priority = order.index(declaration.name) if declaration.name in order else 100
        providers.append(
            CoreMetadataProvider(declaration, user_id, revision, state, values, priority)
        )
    return providers
