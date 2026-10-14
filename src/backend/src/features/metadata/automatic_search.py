"""Host-owned policy for typing-triggered provider work, independent of plugin code."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.app_integration_settings import AppIntegrationSettings

from .configuration import ConfigurationProvider, configuration_presence


def effective_automatic_search(
    *,
    account_enabled: bool,
    provider_enabled: bool,
    server_enabled: bool,
    uses_personal_credentials: bool,
) -> tuple[bool, str | None]:
    """Personal credentials override server limits, never the account master switch."""
    if not account_enabled:
        return False, "account_disabled"
    if not provider_enabled:
        return False, "provider_disabled"
    if not server_enabled and not uses_personal_credentials:
        return False, "server_disabled"
    return True, None


async def server_policy(db: AsyncSession) -> dict[str, bool]:
    row = await db.scalar(select(AppIntegrationSettings).limit(1))
    return dict(row.metadata_automatic_search_policy or {}) if row else {}


async def save_server_policy(
    db: AsyncSession, provider_id: str | None, enabled: bool
) -> dict[str, bool]:
    row = await db.scalar(select(AppIntegrationSettings).limit(1).with_for_update())
    if row is None:
        row = AppIntegrationSettings()
        db.add(row)
    row.metadata_automatic_search_policy = {
        **(row.metadata_automatic_search_policy or {}),
        provider_id or "*": enabled,
    }
    await db.commit()
    return dict(row.metadata_automatic_search_policy)


async def automatic_search_status(
    db: AsyncSession, provider: ConfigurationProvider
) -> dict[str, bool | str | None]:
    preferences = await load_preferences(db, provider.user_id)
    policy = await server_policy(db)
    presence = await configuration_presence(db, provider)
    credential_fields = [
        field for field in provider.declaration.configuration if field.secret or field.required
    ]
    # A partial personal credential must not unlock calls that still consume a
    # shared secret. Scope precedence is the same as configuration_values().
    used = [presence[field.key] for field in credential_fields if any(presence[field.key].values())]
    personal = (
        any(field.secret for field in credential_fields)
        and bool(used)
        and all(field["user"] for field in used)
        and all(presence[field.key]["user"] for field in credential_fields if field.required)
    )
    account_enabled = preferences["metadata_automatic_search_enabled"]
    provider_enabled = preferences["metadata_automatic_search_providers"].get(provider.id, True)
    server_enabled = policy.get("*", True) and policy.get(provider.id, True)
    enabled, reason = effective_automatic_search(
        account_enabled=account_enabled,
        provider_enabled=provider_enabled,
        server_enabled=server_enabled,
        uses_personal_credentials=personal,
    )
    return {
        "enabled": enabled,
        "account_enabled": account_enabled,
        "provider_enabled": provider_enabled,
        "server_enabled": server_enabled,
        "server_provider_enabled": policy.get(provider.id, True),
        "uses_personal_credentials": personal,
        "reason": reason,
    }
