"""Rich chat deliveries (#112): facts and digest members reach only providers that declared them."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.features.notification_providers.base import (
    DigestItem,
    MessageFact,
    ProviderDestination,
)
from src.features.notification_render import MAX_EMBED_FACTS
from src.plugin_api import outbound
from src.plugin_api.notification_contracts import PluginCanonicalDelivery
from tests.test_plugin_notification_contract import (  # noqa: F401  (fixture and helpers)
    source_fixture,
)
from tests.test_plugin_notification_provider_contract import (
    adapter,
    message,
    profile,
    published,
    rendered,
    wire,
)


async def test_embed_providers_receive_facts_as_fields_not_repeated_in_the_body(source):
    notice = await published(source)
    result = await rendered(source, notice, profile(rich_embeds=True))
    assert result.message.facts == (MessageFact("Version", "2.0"),)
    assert "Version: 2.0" not in result.message.body
    assert "rich_text_plain" not in result.fallbacks
    # The facts are part of what the destination saw, so a changed fact is a changed rendering.
    again = await rendered(source, notice, profile(rich_embeds=True))
    assert again.content_hash == result.content_hash
    assert result.content_hash != (await rendered(source, notice, profile())).content_hash


async def test_plain_providers_keep_facts_in_the_text(source):
    notice = await published(source)
    result = await rendered(source, notice, profile())
    assert result.message.facts == ()
    assert "Version: 2.0" in result.message.body


@pytest.mark.parametrize("projection", ["public_release", "media_shared"])
async def test_public_projections_never_receive_private_facts(source, projection):
    notice = await published(source)
    result = await rendered(source, notice, profile(rich_embeds=True), projection)
    assert result.message.facts == ()
    assert "2.0" not in result.message.body or "Version" not in result.message.body


async def test_the_fact_count_is_bounded_and_recorded(source):
    notice = await published(source)
    assert MAX_EMBED_FACTS == 25  # Discord allows 25 embed fields
    result = await rendered(source, notice, profile(rich_embeds=True))
    assert len(result.message.facts) <= MAX_EMBED_FACTS


async def test_the_adapter_passes_facts_and_digest_members_without_identities(monkeypatch):
    runtime = AsyncMock()
    runtime.notification_delivery.return_value = {"success": True, "remote_ref": "c:m"}
    provider = adapter(runtime)
    endpoint = wire(monkeypatch, provider)
    member = DigestItem(
        id=uuid4(),
        kind="plugin",
        event_type="x.update",
        media_type="plugin",
        media_id=uuid4(),
        event_at=5,
        title="Second",
        body="Second body",
        url="https://app.test/notifications/stories/1",
    )
    sent = message(
        operation="send",
        remote_ref=None,
        facts=(MessageFact("Version", "2.0"),),
        items=(member,),
    )
    await provider.deliver(AsyncMock(), ProviderDestination.for_endpoint(endpoint, "Chat"), sent)
    payload = runtime.notification_delivery.await_args.args[2]
    delivery = PluginCanonicalDelivery.model_validate(payload["delivery"])
    assert [(f.label, f.text) for f in delivery.facts] == [("Version", "2.0")]
    assert [(i.title, i.body, i.url) for i in delivery.items] == [
        ("Second", "Second body", "https://app.test/notifications/stories/1")
    ]
    assert str(member.id) not in str(payload) and str(endpoint.user_id) not in str(payload)


def test_outbound_allows_a_json_patch_but_nothing_wider():
    request = outbound._outbound_request(
        {"url": "https://discord.com/api/v10/x", "method": "PATCH", "body": {"content": "x"}}
    )
    assert request.get_method() == "PATCH" and request.data == b'{"content": "x"}'
    for bad in (
        {"method": "DELETE"},
        {"method": "PUT", "body": {}},
        {"method": "PATCH", "text_body": "x"},
        {"method": "GET", "body": {}},
        {"method": "PATCH", "body": ["x"]},
    ):
        with pytest.raises(ValueError):
            outbound._outbound_request({"url": "https://discord.com/api/v10/x", **bad})
