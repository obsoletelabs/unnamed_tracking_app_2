"""Shared encrypted configuration for native core and optional plugin providers."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.preferences import load_preferences
from src.database.models.plugin_metadata_provider import (
    PluginMetadataProviderRegistration,
    PluginProviderConfiguration,
)
from src.database.models.user_scan_settings import DEFAULT_PROVIDER_ORDER, UserScanSettings
from src.plugin_api.metadata_contracts import MetadataProviderRegistration


class ConfigurationProvider(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def plugin_id(self) -> str: ...
    @property
    def user_id(self) -> UUID: ...
    @property
    def declaration(self) -> MetadataProviderRegistration: ...


async def provider_enabled(db: AsyncSession, user_id: UUID, provider_id: str) -> bool:
    """Metadata opt-outs follow the account, independently of credential ownership."""
    preferences = await load_preferences(db, user_id)
    return preferences["metadata_providers_enabled"].get(provider_id, True) is not False


def _names_provider(entry: object, provider_id: str, name: str) -> bool:
    # Orders saved before provider IDs named providers by display name; IDs contain a dot.
    return entry == provider_id or (isinstance(entry, str) and "." not in entry and entry == name)


def provider_priority(provider_id: str, name: str, preferences: dict[str, Any] | None) -> int:
    """Core and optional providers share one saved order; unlisted providers come last."""
    order = (preferences or {}).get("provider_order", [])
    return next(
        (index for index, entry in enumerate(order) if _names_provider(entry, provider_id, name)),
        100,
    )


async def with_saved_provider_order(
    db: AsyncSession, user_id: UUID, preferences: dict[str, Any] | None
) -> dict[str, Any]:
    """Every metadata path follows the account's saved order unless its caller gives one."""
    if preferences and "provider_order" in preferences:
        return preferences
    saved = await db.scalar(
        select(UserScanSettings.provider_order).where(UserScanSettings.user_id == user_id)
    )
    order = saved if saved is not None else list(DEFAULT_PROVIDER_ORDER)
    return {**(preferences or {}), "provider_order": order}


async def configuration_values(
    db: AsyncSession, registration: PluginMetadataProviderRegistration, user_id: UUID
) -> tuple[dict[str, str], str]:
    """Resolve declared scope precedence without exposing values to public UI responses."""
    declaration = MetadataProviderRegistration.model_validate(registration.declaration)
    rows = list(
        await db.scalars(
            select(PluginProviderConfiguration).where(
                PluginProviderConfiguration.provider_id == registration.provider_id,
                PluginProviderConfiguration.plugin_id == registration.plugin_id,
                PluginProviderConfiguration.scope.in_(["system", str(user_id)]),
            )
        )
    )
    scoped = {row.scope: json.loads(decrypt_secret(row.encrypted_values)) for row in rows}
    result: dict[str, str] = {}
    for field in declaration.configuration:
        scopes = (
            [str(user_id), "system"]
            if field.scope == "both"
            else ["system" if field.scope == "system" else str(user_id)]
        )
        value = next(
            (
                scoped[scope].get(field.key)
                for scope in scopes
                if scoped.get(scope, {}).get(field.key)
            ),
            None,
        )
        if value is not None:
            result[field.key] = value
    revision = hashlib.sha256(
        (
            str(registration.installation_id)
            + "|"
            + "|".join(sorted(row.scope + row.encrypted_values for row in rows))
        ).encode()
    ).hexdigest()
    return result, revision


async def save_configuration(
    db: AsyncSession,
    registration: PluginMetadataProviderRegistration,
    scope: str,
    values: dict[str, str | None],
) -> None:
    """Encrypt a scoped patch; omitted fields survive, explicit null removes a field."""
    # Serialize first writes as well as patches; a missing scoped row cannot be row-locked.
    current = await db.scalar(
        select(PluginMetadataProviderRegistration)
        .where(
            PluginMetadataProviderRegistration.provider_id == registration.provider_id,
            PluginMetadataProviderRegistration.installation_id == registration.installation_id,
            PluginMetadataProviderRegistration.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if current is None:
        raise ValueError("provider installation changed")
    declaration = MetadataProviderRegistration.model_validate(registration.declaration)
    field_scope = "system" if scope == "system" else "user"
    allowed = {
        field.key for field in declaration.configuration if field.scope in {field_scope, "both"}
    }
    if not set(values).issubset(allowed):
        raise ValueError("configuration field does not belong to the selected scope")
    row = await db.scalar(
        select(PluginProviderConfiguration)
        .where(
            PluginProviderConfiguration.provider_id == registration.provider_id,
            PluginProviderConfiguration.scope == scope,
        )
        .with_for_update()
    )
    existing = json.loads(decrypt_secret(row.encrypted_values)) if row else {}
    for key, value in values.items():
        if value is None or value == "":
            existing.pop(key, None)
        elif len(value) > 4096:
            raise ValueError("configuration value exceeds its limit")
        else:
            existing[key] = value
    if row is None:
        row = PluginProviderConfiguration(
            provider_id=registration.provider_id, plugin_id=registration.plugin_id, scope=scope
        )
        db.add(row)
    row.encrypted_values = encrypt_secret(json.dumps(existing))
    row.updated_at = int(time.time())
    await db.commit()


async def configuration_presence(
    db: AsyncSession, provider: ConfigurationProvider
) -> dict[str, dict[str, bool]]:
    """Expose presence per declared scope, never credential values or lengths."""
    rows = await db.scalars(
        select(PluginProviderConfiguration).where(
            PluginProviderConfiguration.provider_id == provider.id,
            PluginProviderConfiguration.plugin_id == provider.plugin_id,
            PluginProviderConfiguration.scope.in_(["system", str(provider.user_id)]),
        )
    )
    scopes = {row.scope: json.loads(decrypt_secret(row.encrypted_values)) for row in rows}
    return {
        field.key: {
            "system": field.scope in {"system", "both"}
            and bool(scopes.get("system", {}).get(field.key)),
            "user": field.scope in {"user", "both"}
            and bool(scopes.get(str(provider.user_id), {}).get(field.key)),
        }
        for field in provider.declaration.configuration
    }
