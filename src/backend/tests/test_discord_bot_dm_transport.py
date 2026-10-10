"""Host-side policy tests for the Discord bot DM transport."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
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
    assert result["destination_kind"] is None
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


def test_notification_delivery_injects_host_verified_recipient_context(monkeypatch) -> None:
    runtime_path = Path(__file__).parents[2] / "plugin-runtime" / "runtime.py"
    monkeypatch.syspath_prepend(str(runtime_path.parent))
    spec = importlib.util.spec_from_file_location("discord_bot_runtime_context_test", runtime_path)
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    try:
        spec.loader.exec_module(runtime)
        user_id = uuid4()
        installation_id = uuid4()
        attempt_id = uuid4()
        action = Mock(return_value={"success": True, "retryable": False})
        registry = SimpleNamespace(
            package=lambda plugin_id: (
                object(),
                {"capabilities": [{"name": "notification_providers.deliver"}]},
            ),
            _item=lambda package: {"installation_id": str(installation_id)},
            supervisor=SimpleNamespace(_authorize_capability=Mock()),
            action=action,
        )
        result = runtime.PluginRegistry.notification_delivery(
            registry,
            "official.discord-bot-notifications",
            "deliver",
            {"delivery": {"title": "Private", "body": "Only for the recipient."}},
            user_id=str(user_id),
            installation_id=str(installation_id),
            attempt_id=str(attempt_id),
        )
        assert result == {"success": True, "retryable": False}
        assert action.call_args.args[2]["_plugin_context"] == {
            "user_id": str(user_id),
            "is_admin": False,
        }

        render_action = Mock(return_value={"style": "embed", "fields": ["title"]})
        registry.action = render_action
        runtime.PluginRegistry.notification_delivery(
            registry,
            "official.discord-bot-notifications",
            "render",
            {"delivery": {"title": "Preview", "body": "No context mutation."}},
            user_id=str(user_id),
            installation_id=str(installation_id),
            attempt_id=str(attempt_id),
            _render_only=True,
        )
        assert "_plugin_context" not in render_action.call_args.args[2]
    finally:
        sys.modules.pop(spec.name, None)
