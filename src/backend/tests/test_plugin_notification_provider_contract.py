"""Plugin API 1.1.14 provider capabilities, edits and render contributions at dispatch."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.database.models.notification import Notification
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.session import SessionLocal
from src.features.notification_contributions import contributed_text, selected_contribution
from src.features.notification_providers.base import (
    MessageAction,
    NotificationMessage,
    ProviderDestination,
    notification_message,
)
from src.features.notification_providers.capabilities import ResolvedProfile, resolve_profile
from src.features.notification_providers.plugin import PluginNotificationProvider
from src.features.notification_render import render_message
from src.plugin_api.notification_contracts import (
    NotificationProviderDefinition,
    PluginCanonicalDelivery,
)
from src.plugin_api.notification_story_contracts import (
    NOTIFICATION_CONTRACT_VERSION,
    NotificationContent,
    NotificationProviderCapabilities,
)
from tests.test_plugin_notification_contract import (  # noqa: F401  (fixture and helpers)
    Source,
    event,
    registration,
    source_fixture,
)

PLUGIN_MODULE = "src.features.notification_providers.plugin"


def capabilities(**changes):
    return {
        "contract_version": NOTIFICATION_CONTRACT_VERSION,
        "links": True,
        "message_updates": "edit",
        **changes,
    }


def definition(**changes):
    document = {
        "destinations": [{"kind": "webhook", "label": "Webhook"}],
        "configure_action": "configure",
        "retire_action": "retire",
        "capabilities": capabilities(),
        "edit_action": "edit",
        **changes,
    }
    return document


# --- declaration ------------------------------------------------------------------------------


def test_updates_and_the_edit_action_are_declared_together():
    assert NotificationProviderDefinition.model_validate(definition()).edit_action == "edit"
    with pytest.raises(ValidationError, match="declared together"):
        NotificationProviderDefinition.model_validate(definition(edit_action=None))
    with pytest.raises(ValidationError, match="declared together"):
        NotificationProviderDefinition.model_validate(
            definition(capabilities=capabilities(message_updates="none"))
        )
    plain = definition(capabilities=capabilities(message_updates="none"), edit_action=None)
    assert NotificationProviderDefinition.model_validate(plain).capabilities is not None
    assert (
        NotificationProviderDefinition.model_validate(
            {k: v for k, v in definition().items() if k not in {"capabilities", "edit_action"}}
        ).capabilities
        is None
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"trust": "SECURE"},
        {"verification_supported": True},
        {"recovery_supported": True},
        {"sensitive_supported": True},
        {"max_batch_items": 51},
        {"contract_version": "2.0.0"},
        {"action_buttons": 3, "links": False},
    ],
)
def test_capability_declarations_cannot_claim_authority_or_exceed_bounds(changes):
    with pytest.raises(ValidationError):
        NotificationProviderDefinition.model_validate(
            definition(capabilities=capabilities(**changes))
        )


def test_the_edit_action_is_a_plain_identifier():
    with pytest.raises(ValidationError):
        NotificationProviderDefinition.model_validate(definition(edit_action="Edit Now"))


# --- adapter ------------------------------------------------------------------------------------


def adapter(runtime=None, **changes):
    registration_row = PluginNotificationProviderRegistration(
        plugin_id="example.chat",
        installation_id=uuid4(),
        provider_id="example.chat.hooks",
        name="Chat",
        action_id="deliver",
        transport="plugin",
        definition=definition(**changes),
        server_revision=1,
        server_configured=True,
    )
    return PluginNotificationProvider(registration_row, runtime=runtime or AsyncMock())


def test_edit_exists_only_when_the_plugin_declared_message_updates():
    editable = adapter()
    assert callable(editable.edit)
    profile = resolve_profile(editable)
    assert profile.canonical and profile.can_edit and profile.notes == ()
    fixed = adapter(capabilities=capabilities(message_updates="none"), edit_action=None)
    assert not hasattr(fixed, "edit")
    assert resolve_profile(fixed).canonical and not resolve_profile(fixed).can_edit
    undeclared = adapter(capabilities=None, edit_action=None)
    assert undeclared.capabilities is None and not hasattr(undeclared, "edit")
    assert not resolve_profile(undeclared).canonical


def wire(monkeypatch, provider):
    endpoint = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        revision=3,
        kind="webhook",
        display_name=None,
        installation_id=provider.registration.installation_id,
        privacy=0,
        channel_context="external",
        encrypted_configuration=None,
    )
    endpoint.configuration_ref = str(endpoint.id)
    monkeypatch.setattr(
        f"{PLUGIN_MODULE}.revalidate_delivery_attempt", AsyncMock(return_value=endpoint)
    )
    monkeypatch.setattr(
        f"{PLUGIN_MODULE}.notification_url", AsyncMock(return_value="https://app.test")
    )
    monkeypatch.setattr(provider, "is_authorized", AsyncMock(return_value=True))
    return endpoint


def message(**changes):
    values = {
        "id": uuid4(),
        "kind": "plugin",
        "title": "Update ready",
        "body": "Version 2.0",
        "media_type": "plugin",
        "media_id": uuid4(),
        "event_at": 1,
        "attempt_id": uuid4(),
        "operation": "edit",
        "remote_ref": "msg-1",
        "revision": 2,
        "idempotency_key": "obligation-1",
        "html": "<p>Version 2.0</p>",
        "actions": (MessageAction("Open", "https://example.com/open"),),
        **changes,
    }
    return NotificationMessage(**values)


async def test_an_edit_reaches_the_plugin_with_the_edit_action_and_host_sanitised_content(
    monkeypatch,
):
    runtime = AsyncMock()
    runtime.notification_delivery.return_value = {"success": True, "remote_ref": "msg-2"}
    provider = adapter(runtime)
    endpoint = wire(monkeypatch, provider)
    destination = ProviderDestination.for_endpoint(endpoint, "Chat")
    result = await provider.edit(AsyncMock(), destination, message())
    assert result.success and result.remote_ref == "msg-2" and not result.gone
    _, action_id, payload = runtime.notification_delivery.await_args.args
    assert action_id == "edit"
    delivery = PluginCanonicalDelivery.model_validate(payload["delivery"])
    assert delivery.operation == "edit" and delivery.remote_ref == "msg-1"
    assert delivery.revision == 2 and delivery.idempotency_key == "obligation-1"
    assert delivery.html == "<p>Version 2.0</p>"
    assert [(a.label, a.url) for a in delivery.actions] == [("Open", "https://example.com/open")]
    # The plugin never learns the account or any credential; the destination is an opaque reference.
    assert set(payload) == {"delivery", "destination"}
    assert set(payload["destination"]) == {"id", "revision", "kind"}
    assert str(endpoint.user_id) not in str(payload)
    assert runtime.notification_delivery.await_args.kwargs["user_id"] == str(endpoint.user_id)


async def test_a_refused_edit_is_reported_as_gone_for_a_host_follow_up(monkeypatch):
    runtime = AsyncMock()
    runtime.notification_delivery.return_value = {"success": False, "gone": True, "error": "x"}
    provider = adapter(runtime)
    endpoint = wire(monkeypatch, provider)
    result = await provider.edit(
        AsyncMock(), ProviderDestination.for_endpoint(endpoint, "Chat"), message()
    )
    assert not result.success and result.gone
    assert result.error == "provider_delivery_failed"  # plugin text never reaches host records


async def test_sends_and_follow_ups_use_the_delivery_action(monkeypatch):
    runtime = AsyncMock()
    runtime.notification_delivery.return_value = {"success": True, "remote_ref": "msg-9"}
    provider = adapter(runtime)
    endpoint = wire(monkeypatch, provider)
    destination = ProviderDestination.for_endpoint(endpoint, "Chat")
    for operation in ("send", "follow_up"):
        result = await provider.deliver(
            AsyncMock(), destination, message(operation=operation, remote_ref=None)
        )
        assert result.success and result.remote_ref == "msg-9"
        assert runtime.notification_delivery.await_args.args[1] == "deliver"
        sent = runtime.notification_delivery.await_args.args[2]["delivery"]
        assert sent["operation"] == operation


async def test_providers_without_declarations_keep_the_minimal_legacy_payload(monkeypatch):
    runtime = AsyncMock()
    runtime.notification_delivery.return_value = {"success": True}
    provider = adapter(runtime, capabilities=None, edit_action=None)
    endpoint = wire(monkeypatch, provider)
    result = await provider.deliver(
        AsyncMock(), ProviderDestination.for_endpoint(endpoint, "Chat"), message(operation="send")
    )
    assert result.success and result.remote_ref is None
    sent = runtime.notification_delivery.await_args.args[2]["delivery"]
    assert set(sent) == {
        "notification_id",
        "event_type",
        "title",
        "body",
        "event_at",
        "urgency",
        "link",
    }


async def test_host_owned_test_messages_never_use_the_canonical_payload(monkeypatch):
    runtime = AsyncMock()
    runtime.notification_delivery.return_value = {"success": True}
    provider = adapter(runtime)
    endpoint = wire(monkeypatch, provider)
    await provider.deliver(
        AsyncMock(),
        ProviderDestination.for_endpoint(endpoint, "Chat"),
        message(purpose="test", operation="send", remote_ref=None),
    )
    sent = runtime.notification_delivery.await_args.args[2]["delivery"]
    assert "operation" not in sent and "html" not in sent


def test_plugin_results_may_carry_a_bounded_reference_only():
    from src.plugin_api.contracts import NotificationDeliveryResult

    assert NotificationDeliveryResult.model_validate({"success": True, "remote_ref": "m1"})
    for bad in ({"remote_ref": ""}, {"remote_ref": "x" * 513}, {"credentials": "x"}):
        with pytest.raises(ValidationError):
            NotificationDeliveryResult.model_validate({"success": True, **bad})


# --- render contributions at dispatch -------------------------------------------------------


def profile(*formats, **changes):
    values = {
        "contract_version": NOTIFICATION_CONTRACT_VERSION,
        "text_formats": ("plain", *formats),
        "links": True,
        **changes,
    }
    return ResolvedProfile("canonical", NotificationProviderCapabilities(**values), False, ())


async def published(source, **changes):
    await source.register(**changes)
    await source.publish(event(source.plugin))
    async with SessionLocal() as db:
        return await db.scalar(select(Notification).where(Notification.user_id == source.owner))


async def rendered(source, notice, resolved, projection="canonical"):
    async with SessionLocal() as db:
        base = notification_message(notice, projection=projection, attempt_id=uuid4())
        return await render_message(
            db,
            base,
            notice,
            projection=projection,
            profile=resolved,
            operation="send",
            remote_ref=None,
            link_base="https://app.example.test",
            revision=1,
            idempotency_key=uuid4(),
        )


async def test_a_declared_markdown_contribution_shapes_a_chat_body(source):
    notice = await published(source)
    result = await rendered(source, notice, profile("markdown", rich_embeds=True))
    assert result.message.body == "**Update available** 2\\.0"
    assert "contribution_unsupported" not in result.fallbacks
    # A surface that cannot show Markdown gets plain text, with the downgrade recorded.
    plain = await rendered(source, notice, profile(rich_embeds=True))
    assert plain.message.body == "Update available 2.0"
    assert "contribution_markdown_to_plain" in plain.fallbacks


async def test_contributions_never_apply_to_public_or_shared_projections(source):
    notice = await published(source)
    for projection in ("public_release", "media_shared"):
        result = await rendered(source, notice, profile("markdown", rich_embeds=True), projection)
        assert "2.0" not in result.message.body or "**" not in result.message.body
        assert result.message.body != "**Update available** 2\\.0"


async def test_revoked_grants_and_disabled_sources_fall_back_to_host_rendering(source):
    notice = await published(source)
    chat = profile("markdown", rich_embeds=True)
    async with SessionLocal() as db:
        assert await selected_contribution(db, notice, chat) is not None
    await source.revoke("notifications.canonical")
    async with SessionLocal() as db:
        assert await selected_contribution(db, notice, chat) is None
    result = await rendered(source, notice, chat)
    assert result.message.body != "**Update available** 2\\.0"


async def test_html_templates_are_sanitised_and_need_their_own_grant(source):
    html = {
        "contract_version": NOTIFICATION_CONTRACT_VERSION,
        "event_type": f"{source.plugin}.update",
        "surface": "email",
        "kind": "html_template",
        "template": '<h2>{title}</h2><a href="http://evil.example/x" style="position:fixed">'
        '{fact_version}</a><a href="https://ok.example/y">ok</a>',
    }
    notice = await published(source, renderers=[html])
    email = profile("html_email")
    result = await rendered(source, notice, email)
    assert "<h2>Update available</h2>" in result.message.html
    assert "http://evil.example" not in result.message.html
    assert "position" not in result.message.html
    assert 'href="https://ok.example/y"' in result.message.html
    await source.revoke("notifications.templates.html")
    fallback = await rendered(source, notice, email)
    assert "<h2>" not in fallback.message.html  # host layout, not the plugin's template


def test_fields_contributions_arrange_only_existing_content():
    content = NotificationContent.model_validate(event("example.test")["content"])
    contribution = SimpleNamespace(kind="fields", fields=("facts", "title"), template=None)
    body, note = contributed_text(contribution, content, profile())
    assert body == "Version: 2.0\n\nUpdate available" and note is None
    assert time.time() > 0
