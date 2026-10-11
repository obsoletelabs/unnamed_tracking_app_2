"""Write-only configuration is scoped by core authority and fails closed across races."""

import copy
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notification_enrollment import EnrollmentError
from src.features.notification_plugin_configuration import (
    configure_destination,
    configure_server,
    remove_destination,
    validate_values,
)
from src.features.notification_settings import routing_settings
from src.plugin_api.contracts import NotificationProviderDefinition
from src.plugin_api.gateway import _register_provider
from tests import test_notification_generic_providers as generic_fixtures
from tests.test_notification_webhooks import api, release, webhook_provider  # noqa: F401

generic_provider = generic_fixtures.generic_provider
DEFINITION = generic_fixtures.DEFINITION
registration = generic_fixtures.registration


@pytest.fixture
def configuration_runtime(generic_provider, monkeypatch):  # noqa: F811
    runtime = generic_provider.runtime
    runtime.notification_provider_action.return_value = {"configured": True}
    monkeypatch.setattr(
        "src.features.notification_plugin_configuration.PluginRuntimeClient", lambda: runtime
    )
    return runtime


@pytest.mark.asyncio
async def test_configuration_is_write_only_and_owner_bound(generic_provider, configuration_runtime):
    p = generic_provider
    async with SessionLocal() as db:
        identity = await configure_destination(
            db, p.owner, p.provider, "webhook", {"url": "user-secret-value"}, label="My channel"
        )
        endpoint = await db.get(NotificationDestination, identity)
        assert endpoint.active and endpoint.enabled and endpoint.privacy == 0
        assert endpoint.configuration_ref == str(identity)
        assert endpoint.encrypted_configuration is None
        assert endpoint.user_id == p.owner
        call = configuration_runtime.notification_provider_action.await_args
        assert call.args[2] == {"url": "user-secret-value"}
        assert call.kwargs["context"] == {
            "operation": "configure",
            "scope": "destination",
            "provider_id": p.provider,
            "destination": {"id": str(identity), "revision": 1, "kind": "webhook"},
        }
        assert str(p.owner) not in json.dumps(call.kwargs["context"])
        assert "user-secret-value" not in json.dumps(await routing_settings(db, p.owner))


@pytest.mark.asyncio
async def test_secret_change_withdraws_old_work_before_callback(
    generic_provider, configuration_runtime
):
    p = generic_provider
    async with SessionLocal() as db:
        await release(db, p)

        async def configured(*_args, **_kwargs):
            async with SessionLocal() as observer:
                endpoint = await observer.get(NotificationDestination, p.endpoint)
                assert not endpoint.active and not endpoint.enabled and endpoint.revision == 2
                work = await observer.scalar(
                    select(NotificationDelivery).where(
                        NotificationDelivery.destination_id == p.endpoint
                    )
                )
                assert work.status == "suppressed"
            return {"configured": True}

        configuration_runtime.notification_provider_action.side_effect = configured
        await configure_destination(
            db,
            p.owner,
            p.provider,
            "webhook",
            {"url": "replacement-secret"},
            label="Changed",
            destination_id=p.endpoint,
        )
        endpoint = await db.get(NotificationDestination, p.endpoint)
        assert endpoint.active and endpoint.revision == 2
        assert endpoint.media_consent_revision is None and endpoint.verified_revision is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["failed", "revision", "grant", "registration"])
async def test_callback_cannot_reactivate_failed_or_stale_configuration(
    generic_provider, configuration_runtime, failure
):
    p = generic_provider

    async def configure(*_args, **_kwargs):
        async with SessionLocal() as other:
            if failure == "revision":
                endpoint = await other.get(NotificationDestination, p.endpoint)
                endpoint.revision += 1
            elif failure == "grant":
                grant = await other.scalar(
                    select(PluginPermissionGrant).where(PluginPermissionGrant.plugin_id == p.plugin)
                )
                grant.revoked_at = int(time.time())
            elif failure == "registration":
                row = await other.scalar(
                    select(PluginNotificationProviderRegistration).where(
                        PluginNotificationProviderRegistration.provider_id == p.provider
                    )
                )
                row.action_id = "changed"
            await other.commit()
        return {"configured": failure != "failed", "error": "secret-never-show"}

    configuration_runtime.notification_provider_action.side_effect = configure
    async with SessionLocal() as db:
        with pytest.raises(EnrollmentError) as rejected:
            await configure_destination(
                db,
                p.owner,
                p.provider,
                "webhook",
                {"url": "secret-never-show"},
                label="Channel",
                destination_id=p.endpoint,
            )
        assert "secret-never-show" not in str(rejected.value)
        await db.rollback()
        endpoint = await db.get(NotificationDestination, p.endpoint, populate_existing=True)
        assert not endpoint.active and not endpoint.enabled


@pytest.mark.asyncio
async def test_http_rejects_forged_trust_and_foreign_owner(generic_provider, configuration_runtime):
    p = generic_provider
    async with SessionLocal() as db:
        async with api(db, await db.get(User, p.owner)) as client:
            value = {"provider_id": p.provider, "kind": "webhook", "values": {"url": "secret"}}
            response = await client.post(
                "/api/settings/notification-providers/plugin-destinations",
                json={**value, "privacy": "SECURE"},
            )
            assert response.status_code == 422
            response = await client.put(
                f"/api/settings/notification-providers/plugin-destinations/{uuid4()}", json=value
            )
            assert response.status_code == 404
            response = await client.put(
                f"/api/settings/notification-providers/plugin-server-configuration/{p.provider}",
                json={"values": {}},
            )
            assert response.status_code == 403
        configuration_runtime.notification_provider_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_retired_provider_does_not_hand_secrets_to_reinstallation(
    generic_provider, configuration_runtime
):
    p = generic_provider
    async with SessionLocal() as db:
        row = await db.scalar(
            select(PluginNotificationProviderRegistration).where(
                PluginNotificationProviderRegistration.provider_id == p.provider
            )
        )
        row.installation_id = uuid4()
        await db.commit()
        assert not await remove_destination(db, p.owner, p.endpoint)
        endpoint = await db.get(NotificationDestination, p.endpoint)
        assert not endpoint.active and endpoint.endpoint_key.startswith("retired:")
        configuration_runtime.notification_provider_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_server_configuration_is_admin_only_and_retires_routes(
    generic_provider, configuration_runtime
):
    p = generic_provider
    definition = copy.deepcopy(DEFINITION)
    definition["server_fields"] = [
        {
            "id": "token",
            "label": "Server token",
            "type": "password",
            "secret": True,
            "required": True,
        }
    ]
    async with SessionLocal() as db:
        await _register_provider(
            db,
            plugin_id=p.plugin,
            installation_id=p.installation,
            payload=registration(definition, provider_id=p.provider),
        )
        with pytest.raises(EnrollmentError, match="Administrator"):
            await configure_server(db, p.owner, p.provider, {"token": "server-secret"})
        owner = await db.get(User, p.owner)
        owner.is_admin = True
        await db.commit()
        await configure_server(db, p.owner, p.provider, {"token": "server-secret"})
        row = await db.scalar(
            select(PluginNotificationProviderRegistration).where(
                PluginNotificationProviderRegistration.provider_id == p.provider
            )
        )
        assert row.server_configured and row.server_revision >= 2
        assert "server-secret" not in json.dumps(row.definition)
        endpoint = await db.get(NotificationDestination, p.endpoint, populate_existing=True)
        assert not endpoint.active
        assert (
            configuration_runtime.notification_provider_action.await_args.kwargs["context"]["scope"]
            == "server"
        )


@pytest.mark.parametrize("value", [True, float("nan"), 2**60, -1, 11, "five"])
def test_values_apply_declared_types_and_safe_numeric_bounds(value):
    definition = NotificationProviderDefinition.model_validate(
        {
            **DEFINITION,
            "server_fields": [
                {
                    "id": "count",
                    "label": "Count",
                    "type": "number",
                    "validation": {"minimum": 0, "maximum": 10},
                }
            ],
        }
    )
    with pytest.raises(EnrollmentError):
        validate_values(definition.server_fields, {"count": value}, partial=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["admin", "revision"])
async def test_server_callback_cannot_restore_withdrawn_authority(
    generic_provider, configuration_runtime, change
):
    p = generic_provider
    definition = copy.deepcopy(DEFINITION)
    definition["server_fields"] = [
        {"id": "token", "label": "Token", "type": "password", "secret": True, "required": True}
    ]
    async with SessionLocal() as db:
        await _register_provider(
            db,
            plugin_id=p.plugin,
            installation_id=p.installation,
            payload=registration(definition, provider_id=p.provider),
        )
        owner = await db.get(User, p.owner)
        owner.is_admin = True
        await db.commit()

        async def configured(*_args, **_kwargs):
            async with SessionLocal() as other:
                if change == "admin":
                    admin = await other.get(User, p.owner)
                    admin.is_admin = False
                else:
                    row = await other.scalar(
                        select(PluginNotificationProviderRegistration).where(
                            PluginNotificationProviderRegistration.provider_id == p.provider
                        )
                    )
                    row.server_revision += 1
                await other.commit()
            return {"configured": True}

        configuration_runtime.notification_provider_action.side_effect = configured
        with pytest.raises(EnrollmentError):
            await configure_server(db, p.owner, p.provider, {"token": "server-secret"})
        await db.rollback()
        row = await db.scalar(
            select(PluginNotificationProviderRegistration)
            .where(PluginNotificationProviderRegistration.provider_id == p.provider)
            .execution_options(populate_existing=True)
        )
        assert not row.server_configured
