"""Host-side policy tests for the Discord bot DM transport."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.features.notification_providers.plugin import PluginNotificationProvider
from src.features.notification_settings import _provider_settings


def test_bot_dm_is_private_but_not_a_secure_or_recovery_transport() -> None:
    registration = SimpleNamespace(
        provider_id="official.discord-bot-notifications.dm",
        name="Discord Bot DM",
        transport="discord_bot_dm",
        revoked_at=None,
    )
    smtp = SimpleNamespace(configured=False, allows_sensitive=False, tls_mode="none")
    result = _provider_settings(
        "official.discord-bot-notifications.dm",
        registration,
        True,
        smtp,
    )
    assert result["configuration_scope"] == "user"
    assert result["destination_kind"] == "discord_bot_dm"
    assert result["secure_transport"] is False


@pytest.mark.asyncio
async def test_bot_dm_endpoint_requires_owner_provider_and_installation_match() -> None:
    user_id = uuid4()
    installation_id = uuid4()
    provider_id = "official.discord-bot-notifications.dm"
    registration = SimpleNamespace(
        id=uuid4(),
        plugin_id="official.discord-bot-notifications",
        installation_id=installation_id,
        provider_id=provider_id,
        name="Discord Bot DM",
        action_id="deliver",
        transport="discord_bot_dm",
        revoked_at=None,
    )
    provider = PluginNotificationProvider(registration)
    provider.is_authorized = AsyncMock(return_value=True)
    setting = SimpleNamespace(user_id=user_id, provider_id=provider_id, enabled=True)
    endpoint = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        provider_id=provider_id,
        kind="discord_bot_dm",
        active=True,
        enabled=True,
        installation_id=installation_id,
        revision=3,
        display_name="",
    )
    destination = await provider.lookup_endpoint(
        None, SimpleNamespace(id=user_id, is_active=True), setting, endpoint
    )
    assert destination is not None
    assert destination.user_id == user_id
    assert destination.endpoint_id == endpoint.id
    assert destination.endpoint_revision == 3
    assert destination.allows_sensitive is False

    endpoint.installation_id = uuid4()
    assert (
        await provider.lookup_endpoint(
            None, SimpleNamespace(id=user_id, is_active=True), setting, endpoint
        )
        is None
    )
