"""Independent host possession proof and separately granted plugin disclosure."""

import copy
import time
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.core.crypto import decrypt_secret
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_verification import NotificationVerification
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notification_enrollment import EnrollmentError
from src.features.notification_plugin_policy import delivery_projection
from src.features.notification_plugin_verification import (
    confirm_plugin_verification,
    request_plugin_verification,
    revoke_plugin_verification,
    set_plugin_recovery,
)
from src.features.notification_policy import Trust, effective_trust
from src.features.notification_providers.delivery import process_pending_deliveries
from src.plugin_api.capabilities import capability_definition, capability_implies
from src.plugin_api.notification_contracts import NotificationDestinationDeclaration
from tests import test_notification_generic_providers as generic_fixtures
from tests.test_notification_provider_controls import api
from tests.test_notification_webhooks import webhook_provider  # noqa: F401

generic_provider = generic_fixtures.generic_provider


@pytest.fixture
async def private_provider(generic_provider):
    p = generic_provider
    async with SessionLocal() as db:
        row = await db.scalar(
            select(PluginNotificationProviderRegistration).where(
                PluginNotificationProviderRegistration.provider_id == p.provider
            )
        )
        definition = copy.deepcopy(row.definition)
        definition["destinations"][0].update(
            privacy="PRIVATE",
            verification_supported=True,
            sensitive_supported=True,
            recovery_supported=True,
        )
        row.definition = definition
        endpoint = await db.get(NotificationDestination, p.endpoint)
        endpoint.privacy = int(Trust.PRIVATE)
        db.add(
            PluginPermissionGrant(
                plugin_id=p.plugin,
                installation_id=p.installation,
                user_id=p.owner,
                capability="notification_providers.sensitive",
                capability_version=1,
            )
        )
        await db.commit()
    return p


def test_provider_sensitive_grant_is_independent_and_high_risk():
    for grant in ["api.full", "notifications", "notification_providers", "notifications.sensitive"]:
        assert not capability_implies(grant, "notification_providers.sensitive")
    assert capability_definition("notification_providers.sensitive").highly_privileged
    with pytest.raises(ValidationError):
        NotificationDestinationDeclaration(
            kind="public", label="Public", verification_supported=True
        )
    with pytest.raises(ValidationError):
        NotificationDestinationDeclaration(
            kind="private", label="Private", privacy="PRIVATE", recovery_supported=True
        )


@pytest.mark.asyncio
async def test_host_challenge_is_hidden_scoped_delivered_and_single_use(private_provider):
    p = private_provider
    async with SessionLocal() as db:
        endpoint = await db.get(NotificationDestination, p.endpoint)
        assert effective_trust(endpoint) == Trust.PRIVATE
        challenge_id = await request_plugin_verification(db, p.owner, p.endpoint)
        challenge = await db.get(NotificationVerification, challenge_id)
        code = decrypt_secret(challenge.encrypted_code)
        notice = await db.get(Notification, challenge.notification_id)
        assert code not in notice.body and code not in notice.title
        assert not notice.inbox_visible
        work = list(
            await db.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == notice.id
                )
            )
        )
        assert len(work) == 1 and work[0].destination_id == p.endpoint
        assert await process_pending_deliveries(db, limit=5) == 1
        values = p.runtime.notification_delivery.await_args.args[2]
        assert values["destination"]["id"] == str(p.endpoint)
        assert code in values["delivery"]["body"]
        assert values["delivery"]["link"] is None
        assert not await confirm_plugin_verification(
            db, p.owner, p.endpoint, challenge_id, "000000000"
        )
        assert await confirm_plugin_verification(db, p.owner, p.endpoint, challenge_id, code)
        assert effective_trust(endpoint) == Trust.SECURE
        assert challenge.encrypted_code is None
        assert not await confirm_plugin_verification(db, p.owner, p.endpoint, challenge_id, code)


@pytest.mark.asyncio
async def test_proof_for_one_account_does_not_verify_another(private_provider):
    p = private_provider
    async with SessionLocal() as db:
        other_id = uuid4()
        other = NotificationDestination(
            id=other_id,
            user_id=p.owner,
            provider_id=p.provider,
            endpoint_key=str(other_id),
            configuration_ref=str(other_id),
            installation_id=p.installation,
            kind="webhook",
            privacy=1,
            channel_context="external",
        )
        db.add(other)
        await db.commit()
        challenge_id = await request_plugin_verification(db, p.owner, p.endpoint)
        challenge = await db.get(NotificationVerification, challenge_id)
        code = decrypt_secret(challenge.encrypted_code)
        assert not await confirm_plugin_verification(db, p.owner, other_id, challenge_id, code)
        assert effective_trust(other) == Trust.PRIVATE
        assert await confirm_plugin_verification(db, p.owner, p.endpoint, challenge_id, code)
        assert effective_trust(other) == Trust.PRIVATE
        with pytest.raises(EnrollmentError, match="not found"):
            await confirm_plugin_verification(db, uuid4(), p.endpoint, challenge_id, code)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["expired", "revision", "attempts", "revoked", "grant", "grouped"]
)
async def test_changed_authority_cannot_promote_destination(private_provider, change):
    p = private_provider
    async with SessionLocal() as db:
        challenge_id = await request_plugin_verification(db, p.owner, p.endpoint)
        challenge = await db.get(NotificationVerification, challenge_id)
        code = decrypt_secret(challenge.encrypted_code)
        endpoint = await db.get(NotificationDestination, p.endpoint)
        if change == "expired":
            challenge.expires_at = int(time.time()) - 1
        elif change == "revision":
            endpoint.revision += 1
        elif change == "attempts":
            challenge.failures = 5
        elif change == "revoked":
            await revoke_plugin_verification(db, p.owner, p.endpoint)
        elif change == "grouped":
            endpoint.endpoint_key = "plugin:webhook"
        else:
            grant = await db.scalar(
                select(PluginPermissionGrant).where(
                    PluginPermissionGrant.installation_id == p.installation,
                    PluginPermissionGrant.capability == "notification_providers.sensitive",
                )
            )
            grant.revoked_at = int(time.time())
        await db.commit()
        if change in {"grant", "grouped"}:
            with pytest.raises(EnrollmentError):
                await confirm_plugin_verification(db, p.owner, p.endpoint, challenge_id, code)
        else:
            assert not await confirm_plugin_verification(
                db, p.owner, p.endpoint, challenge_id, code
            )
        assert effective_trust(endpoint) == Trust.PRIVATE
        await process_pending_deliveries(db, limit=5)
        p.runtime.notification_delivery.assert_not_awaited()


@pytest.mark.asyncio
async def test_secure_recovery_requires_proof_opt_in_and_live_grant(private_provider):
    p = private_provider
    async with SessionLocal() as db:
        endpoint = await db.get(NotificationDestination, p.endpoint)
        notice = Notification(
            user_id=p.owner,
            kind="security",
            event_type="host.security",
            required_trust=2,
            purpose="security",
        )
        assert await delivery_projection(db, notice, endpoint, {}) is None
        challenge_id = await request_plugin_verification(db, p.owner, p.endpoint)
        code = decrypt_secret((await db.get(NotificationVerification, challenge_id)).encrypted_code)
        assert await confirm_plugin_verification(db, p.owner, p.endpoint, challenge_id, code)
        assert await delivery_projection(db, notice, endpoint, {}) == "canonical"
        notice.purpose = "recovery"
        assert await delivery_projection(db, notice, endpoint, {}) is None
        endpoint.recovery_allowed = True
        assert await delivery_projection(db, notice, endpoint, {}) == "canonical"
        grant = await db.scalar(
            select(PluginPermissionGrant).where(
                PluginPermissionGrant.installation_id == p.installation,
                PluginPermissionGrant.capability == "notification_providers.sensitive",
            )
        )
        grant.revoked_at = int(time.time())
        await db.commit()
        assert await delivery_projection(db, notice, endpoint, {}) is None


@pytest.mark.asyncio
async def test_owner_api_and_recovery_choice_require_current_host_proof(private_provider):
    p = private_provider
    async with SessionLocal() as request_db, api(
        request_db, await request_db.get(User, p.owner)
    ) as client:
        root = f"/api/settings/notification-providers/plugin-destinations/{p.endpoint}"
        denied = await client.put(root + "/recovery", json={"enabled": True})
        assert denied.status_code == 403
        response = await client.post(root + "/verification")
        assert response.status_code == 200
        challenge_id = response.json()["challenge_id"]
        async with SessionLocal() as db:
            challenge = await db.get(NotificationVerification, UUID(challenge_id))
            code = decrypt_secret(challenge.encrypted_code)
        confirmed = await client.post(
            root + "/verification/confirm",
            json={
                "challenge_id": challenge_id,
                "code": code,
            },
        )
        assert confirmed.status_code == 200
        enabled = await client.put(root + "/recovery", json={"enabled": True})
        assert enabled.status_code == 200 and enabled.json()["recovery_allowed"]
        revoked = await client.post(root + "/verification/revoke")
        assert revoked.status_code == 200
    async with SessionLocal() as db:
        endpoint = await db.get(NotificationDestination, p.endpoint)
        assert endpoint.active and endpoint.enabled and effective_trust(endpoint) == Trust.PRIVATE
        assert not endpoint.recovery_allowed
        grant = await db.scalar(
            select(PluginPermissionGrant).where(
                PluginPermissionGrant.installation_id == p.installation,
                PluginPermissionGrant.capability == "notification_providers.sensitive",
            )
        )
        grant.revoked_at = int(time.time())
        await db.commit()
        # An owner may disable recovery even after plugin grant withdrawal.
        await set_plugin_recovery(db, p.owner, p.endpoint, False)
