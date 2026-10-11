"""The provider status gateway reflects core proof without exposing endpoint data."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.core.config import settings
from src.core.crypto import decrypt_secret
from src.core.preferences import save_preferences
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_verification import NotificationVerification
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.session import SessionLocal
from src.features.notification_plugin_verification import (
    confirm_plugin_verification,
    request_plugin_verification,
    revoke_plugin_verification,
    set_plugin_recovery,
)
from src.features.notification_provider_status import destination_status
from src.plugin_api.capabilities import capability_implies
from src.plugin_api.gateway import dispatch_gateway_request
from src.plugin_api.notification_contracts import NotificationDestinationStatusQuery
from tests.test_notification_plugin_verification import (
    generic_provider as generic_provider,
)
from tests.test_notification_plugin_verification import (
    private_provider as private_provider,
)
from tests.test_notification_webhooks import webhook_provider as webhook_provider


async def read(db, provider, **changes):
    return await dispatch_gateway_request(
        db,
        plugin_id=provider.plugin,
        installation_id=provider.installation,
        user_id=provider.owner,
        method="notification_destinations.status",
        capability="notification_destinations.read",
        payload=changes,
    )


async def grant(db, provider):
    db.add(
        PluginPermissionGrant(
            plugin_id=provider.plugin,
            installation_id=provider.installation,
            user_id=provider.owner,
            capability="notification_destinations.read",
            capability_version=1,
        )
    )
    await db.commit()


def test_status_read_is_independent_and_request_cannot_choose_actor_or_installation():
    for capability in [
        "api.full",
        "notifications",
        "notification_providers",
        "notification_providers.deliver",
        "notifications.lifecycle.read",
    ]:
        assert not capability_implies(capability, "notification_destinations.read")
    for field in ["user_id", "installation_id", "destination_id"]:
        with pytest.raises(ValidationError):
            NotificationDestinationStatusQuery.model_validate({field: str(uuid4())})
    for limit in [True, 0, 101]:
        with pytest.raises(ValidationError):
            NotificationDestinationStatusQuery(limit=limit)


async def test_denied_without_explicit_grant_and_after_revocation(private_provider):
    p = private_provider
    async with SessionLocal() as db:
        with pytest.raises(PermissionError):
            await read(db, p)
        await grant(db, p)
        assert len((await read(db, p))["destinations"]) == 1
        row = await db.scalar(
            select(PluginPermissionGrant).where(
                PluginPermissionGrant.plugin_id == p.plugin,
                PluginPermissionGrant.capability == "notification_destinations.read",
            )
        )
        row.revoked_at = 1
        await db.commit()
        with pytest.raises(PermissionError):
            await read(db, p)


async def test_status_tracks_exact_host_challenge_recovery_and_revocation(private_provider):
    p = private_provider
    async with SessionLocal() as db:
        await grant(db, p)
        row = (await read(db, p))["destinations"][0]
        assert row["trust"] == "PRIVATE" and row["recovery_status"] == "verification_required"
        challenge_id = await request_plugin_verification(db, p.owner, p.endpoint)
        challenge = await db.get(NotificationVerification, challenge_id)
        assert await confirm_plugin_verification(
            db, p.owner, p.endpoint, challenge_id, decrypt_secret(challenge.encrypted_code)
        )
        row = (await read(db, p))["destinations"][0]
        assert row["trust"] == "SECURE" and row["recovery_status"] == "recovery_not_allowed"
        await set_plugin_recovery(db, p.owner, p.endpoint, True)
        row = (await read(db, p))["destinations"][0]
        assert row["trust"] == "SECURE" and row["recovery_status"] == "eligible"
        assert row["recovery_allowed"]
        await revoke_plugin_verification(db, p.owner, p.endpoint)
        row = (await read(db, p))["destinations"][0]
        assert row["trust"] == "PRIVATE" and not row["recovery_allowed"]
        assert row["recovery_status"] == "verification_required"


async def test_status_is_minimal_and_scoped_to_current_actor_installation_provider(
    private_provider,
):
    p = private_provider
    async with SessionLocal() as db:
        await grant(db, p)
        endpoint = await db.get(NotificationDestination, p.endpoint)
        endpoint.display_name = "Private address label"
        endpoint.notification_url = "https://private.example.test"
        await db.commit()
        result = await read(db, p)
        assert result["installation_id"] == str(p.installation)
        assert set(result["destinations"][0]) == {
            "id",
            "provider_id",
            "kind",
            "revision",
            "trust",
            "active",
            "enabled",
            "provider_available",
            "verification_available",
            "recovery_allowed",
            "recovery_status",
        }
        assert "Private address" not in json.dumps(result)
        assert "private.example" not in json.dumps(result)
        for identity in [
            (p.plugin, p.installation, uuid4()),
            (p.plugin, uuid4(), p.owner),
            ("foreign", p.installation, p.owner),
        ]:
            assert not (await destination_status(db, *identity, {}))["destinations"]
        assert not (await read(db, p, provider_id="foreign.provider"))["destinations"]
        endpoint.installation_id = uuid4()
        await db.commit()
        assert not (await read(db, p))["destinations"]


async def test_recovery_is_not_claimed_with_stale_proof_disabled_provider_or_missing_grant(
    private_provider,
):
    p = private_provider
    async with SessionLocal() as db:
        await grant(db, p)
        endpoint = await db.get(NotificationDestination, p.endpoint)
        endpoint.verified_revision = endpoint.revision
        endpoint.verification_method = "plugin.code.v1"
        endpoint.recovery_allowed = True
        await db.commit()
        sensitive = await db.scalar(
            select(PluginPermissionGrant).where(
                PluginPermissionGrant.plugin_id == p.plugin,
                PluginPermissionGrant.capability == "notification_providers.sensitive",
            )
        )
        sensitive.revoked_at = 1
        await db.commit()
        row = (await read(db, p))["destinations"][0]
        assert row["trust"] == "SECURE"
        assert row["recovery_status"] == "sensitive_permission_required"
        assert not row["verification_available"]
        endpoint.revision += 1
        await db.commit()
        assert (await read(db, p))["destinations"][0]["trust"] == "PRIVATE"
        p.runtime.plugins.return_value = []
        row = (await read(db, p))["destinations"][0]
        assert row["recovery_status"] == "provider_unavailable"


async def test_bounded_pagination_keeps_inactive_history_and_recovery_respects_policy(
    private_provider, monkeypatch
):
    p = private_provider
    async with SessionLocal() as db:
        await grant(db, p)
        original = await db.get(NotificationDestination, p.endpoint)
        original.verified_revision = original.revision
        original.verification_method = "plugin.code.v1"
        original.recovery_allowed = True
        identity = uuid4()
        db.add(
            NotificationDestination(
                id=identity,
                user_id=p.owner,
                provider_id=p.provider,
                installation_id=p.installation,
                endpoint_key=str(identity),
                configuration_ref=str(identity),
                kind="webhook",
                privacy=1,
                active=False,
                enabled=False,
            )
        )
        await db.commit()
        first = await read(db, p, limit=1)
        second = await read(db, p, limit=1, after=first["next_after"])
        assert {first["destinations"][0]["id"], second["destinations"][0]["id"]} == {
            str(p.endpoint),
            str(identity),
        }
        assert second["next_after"] is None
        await save_preferences(db, p.owner, {"notification_destinations": {str(p.endpoint): False}})
        result = await read(db, p)
        row = next(item for item in result["destinations"] if item["id"] == str(p.endpoint))
        assert row["trust"] == "SECURE" and row["recovery_status"] == "routing_disabled"
        await save_preferences(db, p.owner, {"notification_destinations": {}})
        monkeypatch.setattr(settings, "NOTIFICATION_BLOCKED_PROVIDERS", p.provider)
        row = next(
            item for item in (await read(db, p))["destinations"] if item["id"] == str(p.endpoint)
        )
        assert row["recovery_status"] == "routing_disabled"
