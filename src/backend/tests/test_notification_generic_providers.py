"""Generic declarations and host-controlled handoff preserve routing and compatibility."""

import copy
import json
import time
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.session import SessionLocal
from src.features.notification_destinations import resolve_destinations
from src.features.notification_providers.base import NotificationMessage, ProviderDestination
from src.features.notification_providers.delivery import process_pending_deliveries
from src.features.notification_providers.plugin import PluginNotificationProvider
from src.plugin_api.contracts import NotificationProviderRegistration
from src.plugin_api.gateway import _register_provider, _unregister_provider
from tests.test_notification_webhooks import release, webhook_provider  # noqa: F401

DEFINITION = {
    "destinations": [
        {
            "kind": "webhook",
            "label": "Public channel",
            "fields": [
                {
                    "id": "url",
                    "label": "Webhook",
                    "type": "password",
                    "secret": True,
                    "required": True,
                },
            ],
        }
    ],
    "configure_action": "configure",
    "retire_action": "retire",
    "test_action": "test",
}


def registration(definition=None, **changes):
    return {
        "provider_id": "example.generic.webhook",
        "name": "Generic",
        "action_id": "deliver",
        "transport": "plugin",
        "definition": copy.deepcopy(definition or DEFINITION),
        **changes,
    }


def test_legacy_registration_preserves_defaults_and_protected_contract():
    old = {"provider_id": "example.generic.webhook", "name": "Generic", "action_id": "deliver"}
    assert NotificationProviderRegistration.model_validate(old).transport == "legacy"
    assert (
        NotificationProviderRegistration.model_validate(
            {**old, "transport": "discord_webhook"}
        ).definition
        is None
    )
    assert (
        NotificationProviderRegistration.model_validate(registration())
        .definition.destinations[0]
        .privacy
        == "PUBLIC"
    )


@pytest.mark.parametrize(
    "mutation", ["secure", "secret_default", "duplicate", "regex", "too_many", "features", "legacy"]
)
def test_hostile_provider_descriptors_are_rejected(mutation):
    value = registration()
    destination = value["definition"]["destinations"][0]
    field = destination["fields"][0]
    if mutation == "secure":
        destination["privacy"] = "SECURE"
    elif mutation == "secret_default":
        field["default"] = "never expose this secret"
    elif mutation == "duplicate":
        destination["fields"].append(copy.deepcopy(field))
    elif mutation == "regex":
        field["validation"] = {"pattern": "(a+)+$"}
    elif mutation == "too_many":
        value["definition"]["destinations"] *= 9
    elif mutation == "features":
        value["definition"]["features"] = {"critical_supported": True}
    else:
        value["transport"] = "legacy"
    with pytest.raises(ValidationError):
        NotificationProviderRegistration.model_validate(value)


@pytest.fixture
async def generic_provider(webhook_provider):  # noqa: F811
    provider = webhook_provider
    async with SessionLocal() as db:
        await _unregister_provider(
            db,
            plugin_id=provider.plugin,
            installation_id=provider.installation,
            payload={"provider_id": provider.provider},
        )
        await _register_provider(
            db,
            plugin_id=provider.plugin,
            installation_id=provider.installation,
            payload=registration(provider_id=provider.provider),
        )
        # Routing opt-in alone cannot enroll or claim plugin credentials.
        db.add(
            NotificationProviderSetting(
                user_id=provider.owner, provider_id=provider.provider, enabled=True
            )
        )
        await db.commit()
        assert not any(
            e.provider_id == provider.provider
            for e in await resolve_destinations(db, provider.owner)
        )
        provider.endpoint = uuid4()
        db.add(
            NotificationDestination(
                id=provider.endpoint,
                user_id=provider.owner,
                provider_id=provider.provider,
                endpoint_key=str(provider.endpoint),
                kind="webhook",
                channel_context="external",
                privacy=0,
                installation_id=provider.installation,
                configuration_ref=str(provider.endpoint),
            )
        )
        await db.commit()
    provider.runtime.notification_delivery.return_value = {"success": True}
    return provider


@pytest.mark.asyncio
async def test_generic_public_handoff_is_minimized_and_core_tracks_deliveries(generic_provider):
    provider = generic_provider
    async with SessionLocal() as db:
        await release(db, provider)
        assert await process_pending_deliveries(db, limit=5) == 1
        call = provider.runtime.notification_delivery.await_args
        payload = call.args[2]
        assert set(payload) == {"delivery", "destination"}
        assert payload["destination"] == {
            "id": str(provider.endpoint),
            "revision": 1,
            "kind": "webhook",
        }
        assert "your USD 20 target" not in payload["delivery"]["body"]
        assert set(payload["delivery"]) == {
            "notification_id",
            "event_type",
            "title",
            "body",
            "event_at",
            "urgency",
            "link",
        }
        assert str(provider.owner) not in json.dumps(payload)
        assert "Selected/followed" not in json.dumps(payload)
        provider.runtime.notification_layout.assert_not_awaited()
        provider.runtime.notification_transport.assert_not_awaited()


@pytest.mark.asyncio
async def test_descriptor_changes_deactivate_existing_endpoints_and_queued_work(generic_provider):
    provider = generic_provider
    async with SessionLocal() as db:
        await release(db, provider)
        changed = registration(provider_id=provider.provider)
        changed["definition"]["destinations"][0]["fields"][0]["label"] = (
            "New endpoint configuration"
        )
        await _register_provider(
            db, plugin_id=provider.plugin, installation_id=provider.installation, payload=changed
        )
        endpoint = await db.get(NotificationDestination, provider.endpoint, populate_existing=True)
        assert not endpoint.active and not endpoint.enabled
        work = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.destination_id == provider.endpoint
            )
        )
        assert work.status == "suppressed"
        await process_pending_deliveries(db, limit=5)
        provider.runtime.notification_delivery.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "invalid", "unavailable"])
async def test_generic_plugin_results_cannot_copy_secret_errors(
    generic_provider, monkeypatch, outcome
):
    p = generic_provider
    async with SessionLocal() as db:
        row = await db.scalar(
            select(PluginNotificationProviderRegistration).where(
                PluginNotificationProviderRegistration.provider_id == p.provider
            )
        )
        adapter = PluginNotificationProvider(row, runtime=p.runtime)
        endpoint = await db.get(NotificationDestination, p.endpoint)
        monkeypatch.setattr(
            "src.features.notification_providers.plugin.revalidate_delivery_attempt",
            AsyncMock(return_value=endpoint),
        )
        p.runtime.notification_delivery.return_value = {
            "success": False,
            "retryable": True,
            "error": "secret-credential-value",
        }
        if outcome == "invalid":
            p.runtime.notification_delivery.return_value = {
                "success": "broken",
                "secret": "secret-credential-value",
            }
        if outcome == "unavailable":
            from src.plugin_api.runtime_client import PluginRuntimeUnavailable

            p.runtime.notification_delivery.side_effect = PluginRuntimeUnavailable(
                "secret-credential-value"
            )
        result = await adapter.deliver(
            db,
            ProviderDestination.for_endpoint(endpoint, "Generic"),
            NotificationMessage(
                id=uuid4(),
                kind="game_released",
                title="Approved",
                body="Approved",
                media_type="game",
                media_id=uuid4(),
                event_at=int(time.time()),
                attempt_id=uuid4(),
            ),
        )
        assert not result.success
        assert "secret-credential-value" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revision", "disabled", "consent", "registration"])
async def test_generic_link_resolution_cannot_release_stale_authority(
    generic_provider, monkeypatch, change
):
    p = generic_provider
    async with SessionLocal() as db:
        await release(db, p)

        async def resolve_link(*_args):
            async with SessionLocal() as other:
                endpoint = await other.get(NotificationDestination, p.endpoint)
                if change == "revision":
                    endpoint.revision += 1
                elif change == "disabled":
                    endpoint.enabled = False
                elif change == "consent":
                    endpoint.media_consent_revision = endpoint.revision
                    endpoint.media_consent_at = int(time.time())
                else:
                    row = await other.scalar(
                        select(PluginNotificationProviderRegistration).where(
                            PluginNotificationProviderRegistration.provider_id == p.provider
                        )
                    )
                    row.action_id = "changed"
                await other.commit()
            return "https://example.test"

        monkeypatch.setattr(
            "src.features.notification_providers.plugin.notification_url", resolve_link
        )
        await process_pending_deliveries(db, limit=5)
        p.runtime.notification_delivery.assert_not_awaited()
