"""Protected destinations, approved rendering and post-render routing revocation."""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select, update

from src.core.crypto import decrypt_secret
from src.database.models.game import Game
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notification_controller import NotificationEvent, emit
from src.features.notification_destinations import resolve_destinations, retire_plugin_destinations
from src.features.notification_enrollment import EnrollmentError
from src.features.notification_lifecycle import delete_notice
from src.features.notification_policy import Trust, effective_trust
from src.features.notification_providers.base import NotificationMessage
from src.features.notification_providers.delivery import process_pending_deliveries
from src.features.notification_providers.webhook import discord_payload, normalize_discord_webhook
from src.features.notification_settings import routing_settings
from src.features.notification_webhooks import create_webhook, remove_webhook, update_webhook
from src.plugin_api.gateway import _register_provider, _unregister_provider
from src.plugin_api.notification_contracts import NotificationFieldLayout
from tests.test_notification_provider_controls import api

WEBHOOK = "https://discord.com/api/webhooks/1234567890/" + "a" * 40


@pytest.fixture
async def webhook_provider(monkeypatch):
    owner, installation = uuid4(), uuid4()
    plugin = f"example.webhook-{uuid4().hex}"
    provider = f"{plugin}.discord"
    runtime = AsyncMock()
    runtime.plugins.return_value = [
        {
            "plugin_id": plugin,
            "installation_id": str(installation),
            "api_contract_version": "1.1.2",
            "enabled": True,
            "compatible": True,
            "status": "running",
            "health": "healthy",
        }
    ]
    runtime.notification_layout.return_value = {"style": "embed", "fields": ["title", "body"]}
    runtime.notification_transport.return_value = {"success": True}
    monkeypatch.setattr(
        "src.features.notification_providers.plugin.PluginRuntimeClient", lambda: runtime
    )
    monkeypatch.setattr(
        "src.features.notification_providers.registry.PluginRuntimeClient", lambda: runtime
    )
    async with SessionLocal() as db:
        db.add(
            User(
                id=owner, username=owner.hex, password_hash="unused", email=f"{owner}@example.test"
            )
        )
        await db.flush()
        db.add(
            PluginPermissionGrant(
                plugin_id=plugin,
                installation_id=installation,
                user_id=owner,
                capability="notification_providers.deliver",
                capability_version=1,
            )
        )
        await db.commit()
        await _register_provider(
            db,
            plugin_id=plugin,
            installation_id=installation,
            payload={
                "provider_id": provider,
                "name": "Protected Discord",
                "action_id": "layout",
                "transport": "discord_webhook",
            },
        )
    try:
        yield SimpleNamespace(
            owner=owner,
            plugin=plugin,
            installation=installation,
            provider=provider,
            runtime=runtime,
        )
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(PluginNotificationProviderRegistration).where(
                    PluginNotificationProviderRegistration.plugin_id == plugin
                )
            )
            await db.execute(
                delete(PluginPermissionGrant).where(PluginPermissionGrant.plugin_id == plugin)
            )
            await db.execute(delete(User).where(User.id == owner))
            await db.commit()


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/12345/" + "a" * 40,
        "https://discord.com.evil.test/api/webhooks/12345/" + "a" * 40,
        "https://user:secret@discord.com/api/webhooks/12345/" + "a" * 40,
        WEBHOOK + "?wait=true",
        WEBHOOK + "#secret",
        WEBHOOK.replace("discord.com", "discord.com:443"),
        WEBHOOK.replace("1234567890", "../anything"),
        "https://127.0.0.1/api/webhooks/12345/secret",
    ],
)
def test_webhook_does_not_admit_arbitrary_egress(url):
    with pytest.raises(ValueError):
        normalize_discord_webhook(url)


@pytest.mark.parametrize(
    "plan",
    [
        {"content": "unrelated private content"},
        {"fields": ["user_email"]},
        {"fields": ["title", "title"]},
        {"style": "raw"},
        {"fields": ["title"], "url": "https://evil.test"},
        {"fields": []},
    ],
)
def test_layout_cannot_supply_raw_content_or_unapproved_fields(plan):
    with pytest.raises(ValidationError):
        NotificationFieldLayout.model_validate(plan)


def test_core_payload_contains_only_approved_fields_and_bounded_transport():
    message = NotificationMessage(
        uuid4(), "game_released", "@everyone " + "x" * 500, "body " * 3000, "game", uuid4(), 1
    )
    payload = discord_payload(NotificationFieldLayout(), message, "https://app.example.test")
    assert payload["allowed_mentions"] == {"parse": []}
    embed = payload["embeds"][0]
    assert len(embed["title"]) == 256 and len(embed["description"]) == 4096
    assert embed["url"] == "https://app.example.test/notifications"
    assert str(message.media_id) not in json.dumps(payload)
    assert str(message.id) not in json.dumps(payload)
    plain = discord_payload(NotificationFieldLayout(style="plain"), message, "")
    assert len(plain["content"]) == 2000


async def release(db, provider):
    game = Game(
        id=uuid4(),
        user_id=provider.owner,
        folder_location=uuid4().hex,
        title="Narnia chronicles",
        sort_title="Narnia chronicles",
    )
    db.add(game)
    await db.flush()
    notification_id = await emit(
        db,
        NotificationEvent(
            type="game.price.threshold_hit",
            user_id=provider.owner,
            entity_type="game",
            entity_id=game.id,
            occurred_at=int(time.time()),
            dedupe_key=uuid4().hex,
            data={
                "source": "host",
                "observation_id": uuid4().hex,
                "currency": "USD",
                "amount": "10",
                "previous_amount": "30",
                "threshold": "20",
                "threshold_id": uuid4().hex,
                "market": "US",
                "platform": "Steam",
            },
        ),
    )
    await db.commit()
    return notification_id


@pytest.mark.asyncio
async def test_multiple_owner_webhooks_are_encrypted_public_and_rendered_without_credentials(
    webhook_provider,
):
    p = webhook_provider
    async with SessionLocal() as db:
        assert not any(e.provider_id == p.provider for e in await resolve_destinations(db, p.owner))
        first = await create_webhook(db, p.owner, p.provider, WEBHOOK, "My releases")
        second = await create_webhook(
            db, p.owner, p.provider, WEBHOOK[:-1] + "b", "Another channel"
        )
        endpoint = await db.get(NotificationDestination, first)
        assert effective_trust(endpoint) == Trust.PUBLIC
        assert WEBHOOK not in endpoint.encrypted_configuration
        assert json.loads(decrypt_secret(endpoint.encrypted_configuration))["webhook"] == WEBHOOK
        result = await routing_settings(db, p.owner)
        assert WEBHOOK not in json.dumps(result) and "a" * 40 not in json.dumps(result)
        assert all(
            item["reactivation_available"]
            for item in result["destinations"]
            if item["provider_id"] == p.provider
        )
        assert (
            next(item for item in result["providers"] if item["id"] == p.provider)[
                "configuration_scope"
            ]
            == "user"
        )
        await release(db, p)
        assert await process_pending_deliveries(db, limit=5) == 2
        calls = p.runtime.notification_transport.await_args_list
        assert {call.args[1] for call in calls} == {WEBHOOK, WEBHOOK[:-1] + "b"}
        for call in p.runtime.notification_layout.await_args_list:
            assert WEBHOOK not in str(call) and "a" * 40 not in str(call)
            assert "your USD 20 target" not in str(call)
        assert "Selected/followed" not in json.dumps(calls[0].args[2])
        await update_webhook(db, p.owner, first, {"share_followed_media": True})
        endpoint = await db.get(NotificationDestination, first)
        assert effective_trust(endpoint) == Trust.PUBLIC
        assert endpoint.media_consent_revision == endpoint.revision
        await release(db, p)
        assert await process_pending_deliveries(db, limit=5) == 2
        rich = [
            call.args[2]
            for call in p.runtime.notification_transport.await_args_list
            if call.args[1] == WEBHOOK
        ]
        assert "Selected/followed" in json.dumps(rich[-1])
        await remove_webhook(db, p.owner, second)
        assert (await db.get(NotificationDestination, second)).encrypted_configuration is None


@pytest.mark.asyncio
async def test_another_owner_and_revoked_grant_cannot_configure_webhook(webhook_provider):
    p = webhook_provider
    async with SessionLocal() as db:
        endpoint = await create_webhook(db, p.owner, p.provider, WEBHOOK, "Channel")
        with pytest.raises(EnrollmentError, match="not found"):
            await update_webhook(db, uuid4(), endpoint, {"share_followed_media": True})
        await db.rollback()
        await db.execute(
            update(PluginPermissionGrant)
            .where(PluginPermissionGrant.plugin_id == p.plugin)
            .values(revoked_at=int(time.time()))
        )
        await db.commit()
        with pytest.raises(EnrollmentError, match="not permitted"):
            await create_webhook(db, p.owner, p.provider, WEBHOOK[:-1] + "b", "Another")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disabled", "consent", "deleted", "grant", "unregistered"])
async def test_renderer_completion_cannot_release_revoked_work(webhook_provider, change):
    p = webhook_provider
    async with SessionLocal() as db:
        endpoint_id = await create_webhook(db, p.owner, p.provider, WEBHOOK, "Channel")
        await update_webhook(db, p.owner, endpoint_id, {"share_followed_media": True})
        notice_id = await release(db, p)

        async def render(*_args, **_kwargs):
            async with SessionLocal() as other:
                if change == "deleted":
                    await delete_notice(other, p.owner, notice_id)
                    await other.commit()
                elif change == "grant":
                    await other.execute(
                        update(PluginPermissionGrant)
                        .where(PluginPermissionGrant.plugin_id == p.plugin)
                        .values(revoked_at=int(time.time()))
                    )
                    await other.commit()
                elif change == "unregistered":
                    await _unregister_provider(
                        other,
                        plugin_id=p.plugin,
                        installation_id=p.installation,
                        payload={"provider_id": p.provider},
                    )
                else:
                    await update_webhook(
                        other,
                        p.owner,
                        endpoint_id,
                        {"enabled": False}
                        if change == "disabled"
                        else {"share_followed_media": False},
                    )
            return {"style": "embed", "fields": ["title", "body"]}

        p.runtime.notification_layout.side_effect = render
        assert await process_pending_deliveries(db, limit=1) == 0
        p.runtime.notification_transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_renderer_and_failure_do_not_block_other_endpoint(webhook_provider):
    p = webhook_provider
    async with SessionLocal() as db:
        await create_webhook(db, p.owner, p.provider, WEBHOOK, "Channel")
        await create_webhook(db, p.owner, p.provider, WEBHOOK[:-1] + "b", "Another")
        notice = await release(db, p)
        p.runtime.notification_layout.side_effect = [
            {"content": "Private unrelated string"},
            {"style": "plain", "fields": ["title"]},
        ]
        assert await process_pending_deliveries(db, limit=2) == 1
        assert p.runtime.notification_transport.await_count == 1
        statuses = set(
            await db.scalars(
                select(NotificationDelivery.status).where(
                    NotificationDelivery.notification_id == notice,
                    NotificationDelivery.provider_id == p.provider,
                )
            )
        )
        assert statuses == {"sent", "failed_permanent"}
        assert (await db.get(Notification, notice)).body == "Price reached your USD 20 target: 10"


@pytest.mark.asyncio
async def test_provider_reinstall_retains_inactive_history_without_reclaiming_consent(
    webhook_provider,
):
    p = webhook_provider
    async with SessionLocal() as db:
        old_id = await create_webhook(db, p.owner, p.provider, WEBHOOK, "Channel")
        await update_webhook(db, p.owner, old_id, {"share_followed_media": True})
        await _unregister_provider(
            db,
            plugin_id=p.plugin,
            installation_id=p.installation,
            payload={"provider_id": p.provider},
        )
        fresh = uuid4()
        await _register_provider(
            db,
            plugin_id=p.plugin,
            installation_id=fresh,
            payload={
                "provider_id": p.provider,
                "name": "Protected Discord",
                "action_id": "layout",
                "transport": "discord_webhook",
            },
        )
        old = await db.get(NotificationDestination, old_id)
        assert not old.active and not old.enabled and old.media_consent_revision is None
        assert old.installation_id == p.installation
        p.runtime.plugins.return_value[0]["installation_id"] = str(fresh)
        db.add(
            PluginPermissionGrant(
                plugin_id=p.plugin,
                installation_id=fresh,
                user_id=p.owner,
                capability="notification_providers.deliver",
                capability_version=1,
            )
        )
        await db.commit()
        metadata = await routing_settings(db, p.owner)
        assert not next(d for d in metadata["destinations"] if d["id"] == str(old_id))[
            "reactivation_available"
        ]
        new_id = await create_webhook(db, p.owner, p.provider, WEBHOOK, "Fresh enrollment")
        assert new_id != old_id
        new = await db.get(NotificationDestination, new_id)
        assert new.media_consent_revision is None and effective_trust(new) == Trust.PUBLIC


@pytest.mark.asyncio
async def test_http_enrollment_rejects_forged_trust_and_owner_routing(webhook_provider):
    p = webhook_provider
    async with SessionLocal() as db:
        async with api(db, await db.get(User, p.owner)) as client:
            base = "/api/settings/notification-providers/webhook-destinations"
            data = {"provider_id": p.provider, "url": WEBHOOK, "label": "Channel"}
            response = await client.post(base, json={**data, "privacy": 2})
            assert response.status_code == 422
            response = await client.post(base, json=data)
            assert response.status_code == 201 and WEBHOOK not in response.text
            identity = response.json()["id"]
            assert (
                await client.patch(f"{base}/{identity}", json={"verified_revision": 1})
            ).status_code == 422
            assert (
                await client.patch(f"{base}/{uuid4()}", json={"enabled": True})
            ).status_code == 404
            assert (
                await client.patch(f"{base}/{identity}", json={"share_followed_media": True})
            ).status_code == 200
            assert (await client.delete(f"{base}/{identity}")).status_code == 200


@pytest.mark.asyncio
async def test_disable_requires_explicit_same_installation_activation_and_never_replays(
    webhook_provider,
):
    p = webhook_provider
    async with SessionLocal() as db:
        identity = await create_webhook(db, p.owner, p.provider, WEBHOOK, "Channel")
        await update_webhook(db, p.owner, identity, {"share_followed_media": True})
        notice = await release(db, p)
        await retire_plugin_destinations(db, p.plugin)
        await db.commit()
        destination = await db.get(NotificationDestination, identity)
        assert not destination.active and destination.media_consent_revision is None
        await update_webhook(db, p.owner, identity, {"enabled": True})
        destination = await db.get(NotificationDestination, identity)
        assert (
            destination.active
            and destination.enabled
            and destination.media_consent_revision is None
        )
        assert await process_pending_deliveries(db, limit=3) == 0
        statuses = set(
            await db.scalars(
                select(NotificationDelivery.status).where(
                    NotificationDelivery.notification_id == notice,
                    NotificationDelivery.provider_id == p.provider,
                )
            )
        )
        assert statuses == {"suppressed"}
        assert await release(db, p)
        assert await process_pending_deliveries(db, limit=3) == 1
