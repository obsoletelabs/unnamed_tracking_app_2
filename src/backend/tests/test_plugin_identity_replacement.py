"""Replacing a package with another publisher's signature must not inherit notification proof."""

from __future__ import annotations

import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from test_plugin_install_sources import (  # noqa: F401
    PASSWORD,
    acquire,
    grants,
    package_bytes,
    plugin_gate,
    seed_update,
)

from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_auth_flow import AuthRecoveryToken
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.plugin_api.installer import (
    PackageTrust,
    PackageTrustStatus,
    signing_identity_replaced,
)
from src.plugin_api.updates import TrustedPublisher

REPLACE = {"operation": "replace"}


def _trust(status: PackageTrustStatus, key_id: str | None) -> PackageTrust:
    return PackageTrust(status, key_id is not None, key_id is not None, key_id, None, None)


@pytest.fixture(name="other_key")
def second_publisher(gate):
    """A genuinely different, throwaway signing key trusted by the same verifier."""
    key = Ed25519PrivateKey.generate()
    gate.verifier.publishers["other"] = TrustedPublisher(
        "other", key.public_key().public_bytes_raw(), "Other publisher"
    )
    return key


def replacement(gate, other_key, *, version="2.0.0", permissions=()):
    return package_bytes(
        gate.plugin_id,
        version=version,
        trust="trusted",
        key=other_key,
        key_id="other",
        permissions=permissions,
    )


def same_key_replacement(gate, *, version="2.0.0", permissions=()):
    return package_bytes(
        gate.plugin_id, version=version, trust="trusted", key=gate.key, permissions=permissions
    )


async def seed_notification_state(gate):
    """A verified SECURE destination with recovery, media consent, queued work and a credential."""
    installation_id = UUID(gate.registry.list()[0]["installation_id"])
    provider_id = f"{gate.plugin_id}.discord"
    db = gate.db
    db.add(
        PluginNotificationProviderRegistration(
            plugin_id=gate.plugin_id,
            installation_id=installation_id,
            provider_id=provider_id,
            name="Discord",
            action_id="deliver",
            server_configured=True,
        )
    )
    destination = NotificationDestination(
        id=uuid4(),
        user_id=gate.admin.id,
        provider_id=provider_id,
        endpoint_key="plugin:webhook",
        kind="webhook",
        privacy=1,
        revision=3,
        verified_revision=3,
        verification_method="provider_challenge",
        enabled=True,
        active=True,
        installation_id=installation_id,
        configuration_ref=None,
        encrypted_configuration="encrypted-endpoint",
        recovery_allowed=True,
        media_consent_revision=3,
        media_consent_at=int(time.time()),
    )
    notification = Notification(
        id=uuid4(),
        user_id=gate.admin.id,
        kind="plugin",
        media_type="plugin",
        media_id=uuid4(),
        title="Sensitive",
        body="Sensitive body",
        event_at=int(time.time()),
        dedupe_key=uuid4().hex,
        source=gate.plugin_id,
        source_installation_id=installation_id,
    )
    db.add_all([destination, notification])
    await db.flush()
    delivery = NotificationDelivery(
        notification_id=notification.id,
        provider_id=provider_id,
        destination_id=destination.id,
        destination_revision=3,
        status="pending",
    )
    token = AuthRecoveryToken(
        user_id=gate.admin.id,
        plugin_id=gate.plugin_id,
        installation_id=installation_id,
        flow_revision=1,
        token_hash=uuid4().hex + uuid4().hex,
        encrypted_token="encrypted-recovery-credential",
        account_fingerprint="f" * 64,
        notification_id=notification.id,
        expires_at=int(time.time()) + 3600,
    )
    db.add_all([delivery, token])
    await db.commit()
    return SimpleNamespace(
        installation_id=installation_id,
        destination_id=destination.id,
        delivery_id=delivery.id,
        token_id=token.id,
        provider_id=provider_id,
    )


async def cleanup(gate, seeded):
    await gate.db.rollback()
    await gate.db.execute(
        delete(PluginNotificationProviderRegistration).where(
            PluginNotificationProviderRegistration.plugin_id == gate.plugin_id
        )
    )
    await gate.db.commit()


async def snapshot(gate, seeded):
    gate.db.expire_all()
    destination = await gate.db.get(NotificationDestination, seeded.destination_id)
    delivery = await gate.db.get(NotificationDelivery, seeded.delivery_id)
    token = await gate.db.get(AuthRecoveryToken, seeded.token_id)
    registration = await gate.db.scalar(
        select(PluginNotificationProviderRegistration).where(
            PluginNotificationProviderRegistration.provider_id == seeded.provider_id
        )
    )
    return destination, delivery, token, registration


def assert_proof_intact(destination, delivery, token, registration):
    assert destination.verified_revision == destination.revision == 3
    assert destination.verification_method == "provider_challenge"
    assert destination.enabled and destination.active and destination.recovery_allowed
    assert destination.media_consent_revision == 3
    assert destination.encrypted_configuration == "encrypted-endpoint"
    assert destination.endpoint_key == "plugin:webhook"
    assert delivery.status == "pending"
    assert token.used_at is None and token.encrypted_token
    assert registration.revoked_at is None and registration.server_configured


def assert_proof_retired(destination, delivery, token, registration):
    assert destination.verified_revision is None
    assert destination.verification_method is None
    assert destination.revision > 3
    assert not destination.enabled and not destination.active
    assert destination.recovery_allowed is False
    assert destination.media_consent_revision is None and destination.media_consent_at is None
    assert destination.encrypted_configuration is None
    assert destination.endpoint_key == f"retired:{destination.id}"
    assert delivery.status == "suppressed" and delivery.claim_token is None
    assert token.used_at is not None and token.encrypted_token is None
    assert registration.revoked_at is not None and not registration.server_configured


@pytest.mark.parametrize(
    "installed,candidate,operation,expected",
    [
        (("trusted", "a"), ("trusted", "a"), "update", False),
        (("trusted", "a"), ("trusted", "b"), "replace", True),
        (("trusted", "a"), ("trusted", "b"), "rollback", True),
        (("trusted", "a"), ("trusted", "b"), "reinstall", False),
        (("unsigned", None), ("trusted", "a"), "update", True),
        (("trusted", "a"), ("unsigned", None), "replace", True),
        (("unsigned", None), ("unsigned", None), "replace", False),
    ],
)
def test_signing_identity_replacement_matrix(installed, candidate, operation, expected):
    status = {"trusted": PackageTrustStatus.TRUSTED, "unsigned": PackageTrustStatus.UNSIGNED}
    inspected = SimpleNamespace(trust=_trust(status[candidate[0]], candidate[1]))
    record = {"trust": {"status": installed[0], "publisher_key_id": installed[1]}}
    assert signing_identity_replaced(inspected, record, operation) is expected


async def test_differently_signed_replacement_retires_notification_proof(gate, other_key):
    await seed_update(gate, "update", trust="trusted", permissions=("games.read",))
    seeded = await seed_notification_state(gate)
    try:
        assert_proof_intact(*await snapshot(gate, seeded))
        response = await acquire(
            gate,
            "update",
            replacement(gate, other_key, permissions=("games.read",)),
            approved_permissions=["games.read:v1"],
            **REPLACE,
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "running"
        installed = gate.registry.list()[0]
        # The lifecycle instance continues; its notification trust does not.
        assert UUID(installed["installation_id"]) == seeded.installation_id
        assert installed["trust"]["publisher_key_id"] == "other"
        assert_proof_retired(*await snapshot(gate, seeded))
        assert [row.capability for row in await grants(gate)] == ["games.read"]
    finally:
        await cleanup(gate, seeded)


async def test_retired_work_cannot_be_replayed_by_the_replacement(gate, other_key):
    await seed_update(gate, "update", trust="trusted")
    seeded = await seed_notification_state(gate)
    try:
        await acquire(gate, "update", replacement(gate, other_key), **REPLACE)
        _, delivery, token, _ = await snapshot(gate, seeded)
        assert delivery.status == "suppressed"
        assert token.used_at is not None and token.encrypted_token is None
        # A later same-key update must not revive anything either.
        later = package_bytes(
            gate.plugin_id, version="3.0.0", trust="trusted", key=other_key, key_id="other"
        )
        assert (await acquire(gate, "update", later)).status_code == 200
        destination, delivery, token, _ = await snapshot(gate, seeded)
        assert delivery.status == "suppressed"
        assert token.used_at is not None
        assert not destination.enabled and destination.verified_revision is None
    finally:
        await cleanup(gate, seeded)


async def test_same_key_replacement_and_updates_keep_destination_proof(gate):
    await seed_update(gate, "update", trust="trusted")
    seeded = await seed_notification_state(gate)
    try:
        update = await acquire(gate, "update", same_key_replacement(gate, version="1.1.0"))
        assert update.status_code == 200, update.text
        assert_proof_intact(*await snapshot(gate, seeded))
        replaced = await acquire(gate, "update", same_key_replacement(gate), **REPLACE)
        assert replaced.status_code == 200, replaced.text
        assert_proof_intact(*await snapshot(gate, seeded))
    finally:
        await cleanup(gate, seeded)


async def test_ordinary_update_by_another_publisher_is_still_rejected(gate, other_key):
    await seed_update(gate, "update", trust="trusted")
    seeded = await seed_notification_state(gate)
    try:
        response = await acquire(gate, "update", replacement(gate, other_key))
        assert response.status_code == 409
        assert "publisher" in response.json()["detail"]
        assert "install" not in gate.events
        assert_proof_intact(*await snapshot(gate, seeded))
    finally:
        await cleanup(gate, seeded)


async def test_replacement_preview_warns_before_proof_is_retired(gate, other_key):
    await seed_update(gate, "update", trust="trusted")
    seeded = await seed_notification_state(gate)
    try:
        preview = await gate.client.put(
            f"/api/plugins/{gate.plugin_id}/update/preview",
            params={"operation": "replace"},
            files={"file": ("candidate.utp", replacement(gate, other_key))},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["signing_identity_replaced"] is True
        assert "must be set up and verified again" in preview.json()["identity_warning"]
        assert_proof_intact(*await snapshot(gate, seeded))
    finally:
        await cleanup(gate, seeded)


async def test_database_commit_failure_leaves_previous_destination_state(gate, other_key):
    await seed_update(gate, "update", trust="trusted")
    seeded = await seed_notification_state(gate)
    try:
        gate.runtime.fail_commit = True
        with pytest.raises(IntegrityError):
            await acquire(gate, "update", replacement(gate, other_key), **REPLACE)
        gate.runtime.fail_commit = False
        assert "abort" in gate.events and "start" not in gate.events
        assert gate.registry.list()[0]["trust"]["publisher_key_id"] == "known"
        assert gate.registry.package(gate.plugin_id)[1]["version"] == "1.0.0"
        assert_proof_intact(*await snapshot(gate, seeded))
    finally:
        await cleanup(gate, seeded)


async def test_runtime_preparation_failure_leaves_previous_destination_state(gate, other_key):
    await seed_update(gate, "update", trust="trusted")
    seeded = await seed_notification_state(gate)
    try:
        gate.runtime.fail_install = True
        response = await acquire(gate, "update", replacement(gate, other_key), **REPLACE)
        gate.runtime.fail_install = False
        assert response.status_code == 422
        assert "commit" not in gate.events.copy()[-1:]
        assert gate.registry.list()[0]["trust"]["publisher_key_id"] == "known"
        assert_proof_intact(*await snapshot(gate, seeded))
    finally:
        await cleanup(gate, seeded)


async def test_failed_activation_restores_package_but_never_old_proof(gate, other_key):
    await seed_update(gate, "update", trust="trusted", permissions=("games.read",))
    seeded = await seed_notification_state(gate)
    try:
        gate.runtime.healthy = False
        response = await acquire(
            gate,
            "update",
            replacement(gate, other_key, permissions=("games.read",)),
            approved_permissions=["games.read:v1"],
            **REPLACE,
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "rolled_back"
        assert gate.registry.package(gate.plugin_id)[1]["version"] == "1.0.0"
        # The candidate ran under this installation, so its predecessor's proof stays retired
        # while the previous release and its grants are restored.
        assert_proof_retired(*await snapshot(gate, seeded))
        assert [row.capability for row in await grants(gate)] == ["games.read"]
    finally:
        await cleanup(gate, seeded)


async def test_rollback_to_another_publishers_release_does_not_inherit_state(gate, other_key):
    await seed_update(gate, "update", trust="trusted", permissions=("games.read",))
    replaced = await acquire(
        gate,
        "update",
        replacement(gate, other_key, permissions=("games.read",)),
        approved_permissions=["games.read:v1"],
        **REPLACE,
    )
    assert replaced.status_code == 200, replaced.text
    seeded = await seed_notification_state(gate)
    try:
        rollback = await gate.client.post(f"/api/plugins/{gate.plugin_id}/rollback", json={})
        assert rollback.status_code == 200, rollback.text
        assert gate.registry.list()[0]["trust"]["publisher_key_id"] == "known"
        assert_proof_retired(*await snapshot(gate, seeded))
        # Grants made to the other publisher are not carried to the older release.
        assert await grants(gate) == []
    finally:
        await cleanup(gate, seeded)
