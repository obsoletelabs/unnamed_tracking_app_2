"""Pre-account ownership, normal dispatcher delivery and atomic single-use acceptance."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.api.routes.user_invitations import router
from src.core.auth import hash_password, verify_password
from src.core.config import settings
from src.core.crypto import decrypt_secret
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_auth_flow import AuthRequestLimit, PluginAuthFlowRegistration
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.models.user_invitation import NotificationPendingRecipient, UserInvitation
from src.database.session import SessionLocal
from src.features.notification_policy import effective_trust, select_projection
from src.features.notification_providers import smtp
from src.features.notification_providers.delivery import process_pending_deliveries
from src.features.plugin_auth_flows import register_invite_flow, unregister_invite_flow
from src.features.user_invitations import (
    accept_invitation,
    expire_invitations,
    issue_invitation,
    list_invitations,
    revoke_invitation,
)
from src.plugin_api.capabilities import capability_definition, capability_implies
from src.plugin_api.gateway import dispatch_gateway_request
from src.plugin_api.runtime_client import PluginRuntimeClient
from tests.test_notification_email import mailbox as mailbox


@pytest.fixture
async def invitation(mailbox, monkeypatch):
    admin, installation, grant = uuid4(), uuid4(), uuid4()
    plugin = "test.invite." + installation.hex
    state = [
        {
            "plugin_id": plugin,
            "installation_id": str(installation),
            "enabled": True,
            "compatible": True,
            "status": "running",
            "health": "healthy",
            "api_contract_version": "1.1.8",
        }
    ]
    monkeypatch.setattr(PluginRuntimeClient, "plugins", AsyncMock(side_effect=lambda: state))
    async with SessionLocal() as db:
        await db.execute(delete(PluginAuthFlowRegistration))
        await db.execute(delete(AuthRequestLimit))
        db.add(
            User(
                id=admin,
                username=admin.hex,
                email=f"{admin}@example.test",
                password_hash=hash_password("Admin-Password-123!"),
                is_admin=True,
                last_app_url="https://library.example.test",
            )
        )
        db.add(
            PluginPermissionGrant(
                id=grant,
                plugin_id=plugin,
                installation_id=installation,
                capability="auth.user_invites",
                capability_version=1,
            )
        )
        await db.commit()
        await register_invite_flow(
            db, plugin, installation, admin, {"mode": "user_invite", "name": "Invites"}
        )
    p = SimpleNamespace(
        admin=admin,
        installation=installation,
        grant=grant,
        plugin=plugin,
        email=f"invite-{installation}@example.test",
        messages=mailbox,
        state=state,
    )
    yield p
    async with SessionLocal() as db:
        pending = select(UserInvitation.pending_recipient_id).where(
            UserInvitation.plugin_id == plugin
        )
        endpoints = select(NotificationDestination.id).where(
            NotificationDestination.pending_recipient_id.in_(pending)
        )
        await db.execute(
            delete(NotificationDelivery).where(NotificationDelivery.destination_id.in_(endpoints))
        )
        await db.execute(
            delete(NotificationPendingRecipient).where(NotificationPendingRecipient.id.in_(pending))
        )
        await db.execute(delete(User).where((User.id == admin) | (User.email == p.email)))
        await db.execute(delete(PluginPermissionGrant).where(PluginPermissionGrant.id == grant))
        await db.execute(delete(PluginAuthFlowRegistration))
        await db.execute(delete(AuthRequestLimit))
        await db.commit()


async def issue(p, **fields):
    async with SessionLocal() as db:
        result = await issue_invitation(
            db, p.plugin, p.installation, p.admin, {"email": p.email, **fields}
        )
        row = await db.get(UserInvitation, UUID(result["id"]))
        return row.id, decrypt_secret(row.encrypted_token)


@pytest.mark.asyncio
async def test_invite_is_bootstrap_only_and_secret_is_not_canonical(invitation):
    p = invitation
    identity, secret = await issue(p)
    async with SessionLocal() as db:
        row = await db.get(UserInvitation, identity)
        notice = await db.get(Notification, row.notification_id)
        endpoint = await db.scalar(
            select(NotificationDestination).where(
                NotificationDestination.pending_recipient_id == row.pending_recipient_id
            )
        )
        assert await db.scalar(select(User).where(User.email == p.email)) is None
        assert notice.user_id is endpoint.user_id is None
        assert (
            notice.pending_recipient_id == endpoint.pending_recipient_id == row.pending_recipient_id
        )
        assert not notice.inbox_visible and notice.required_trust == effective_trust(endpoint) == 1
        assert select_projection(notice, endpoint, {}) is None
        assert secret not in json.dumps({"title": notice.title, "body": notice.body})
        assert p.email not in notice.body
        assert await process_pending_deliveries(db) == 1
        delivered = await db.scalar(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == notice.id)
        )
        assert delivered.status == "sent" and delivered.attempts == 1
    assert p.messages[0]["To"] == p.email and not p.messages[0].get("List-Unsubscribe")
    text = p.messages[0].get_body(preferencelist=("plain",)).get_content()
    link = next(line for line in text.splitlines() if "/accept-invite#" in line)
    assert link.startswith("https://library.example.test/accept-invite#")
    assert parse_qs(urlsplit(link).fragment)["token"] == [secret]


@pytest.mark.asyncio
async def test_acceptance_creates_normal_account_once_without_auto_login(invitation):
    p = invitation
    identity, token = await issue(p)
    app = FastAPI()
    app.include_router(router)
    payload = {
        "id": str(identity),
        "token": token,
        "username": "invite-" + p.installation.hex,
        "password": "New-Password-789!",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.get("/api/auth/user-invites/accept", params=payload)
        ).status_code == 405
        assert (
            await client.post("/api/auth/user-invites/accept", json={**payload, "is_admin": True})
        ).status_code == 422
        response = await client.post("/api/auth/user-invites/accept", json=payload)
        assert response.status_code == 200 and not response.headers.get("set-cookie")
        assert (await client.post("/api/auth/user-invites/accept", json=payload)).status_code == 400
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == p.email))
        assert user.is_active and not user.is_admin and user.oidc_subject is None
        assert verify_password(payload["password"], user.password_hash)
        assert not list(
            await db.scalars(
                select(NotificationDestination).where(NotificationDestination.user_id == user.id)
            )
        )
        row = await db.get(UserInvitation, identity)
        assert row.used_at and row.encrypted_token is None
        delivery = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == row.notification_id
            )
        )
        assert delivery.status == "cancelled"


@pytest.mark.asyncio
async def test_wrong_token_policy_and_concurrent_acceptance(invitation):
    p = invitation
    identity, token = await issue(p)
    async with SessionLocal() as db:
        assert not await accept_invitation(
            db, identity, "x" * 43, "new-" + p.installation.hex, "New-Password-789!"
        )
        with pytest.raises(ValueError):
            await accept_invitation(db, identity, token, "new-" + p.installation.hex, "short")

    async def accept():
        async with SessionLocal() as db:
            return await accept_invitation(
                db, identity, token, "new-" + p.installation.hex, "New-Password-789!"
            )

    assert sorted(await asyncio.gather(accept(), accept())) == [False, True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["expiry", "grant", "installation", "revision", "issuer", "recipient", "endpoint"]
)
async def test_late_changes_stop_delivery_and_unauthorized_acceptance(invitation, change):
    p = invitation
    identity, token = await issue(p)
    async with SessionLocal() as db:
        row = await db.get(UserInvitation, identity)
        if change == "expiry":
            row.expires_at = int(time.time()) - 1
        elif change == "grant":
            (await db.get(PluginPermissionGrant, p.grant)).revoked_at = int(time.time())
        elif change == "installation":
            p.state[0]["installation_id"] = str(uuid4())
        elif change == "revision":
            (await db.get(PluginAuthFlowRegistration, "user_invite")).revision += 1
        elif change == "issuer":
            (await db.get(User, p.admin)).is_admin = False
        elif change == "recipient":
            (await db.get(NotificationPendingRecipient, row.pending_recipient_id)).retired_at = int(
                time.time()
            )
        else:
            endpoint = await db.scalar(
                select(NotificationDestination).where(
                    NotificationDestination.pending_recipient_id == row.pending_recipient_id
                )
            )
            endpoint.privacy = 0
        await db.commit()
        assert await process_pending_deliveries(db) == 0
        if change != "endpoint":
            assert not await accept_invitation(
                db, identity, token, "new-" + p.installation.hex, "New-Password-789!"
            )
    assert not p.messages


@pytest.mark.asyncio
async def test_replacement_revocation_and_limits(invitation):
    p = invitation
    first, token = await issue(p)
    second, _ = await issue(p)
    async with SessionLocal() as db:
        assert not await accept_invitation(
            db, first, token, "new-" + p.installation.hex, "New-Password-789!"
        )
        listed = await list_invitations(db, p.plugin, p.installation, p.admin, {})
        assert {row["status"] for row in listed["invitations"]} == {"pending", "revoked"}
        assert token not in json.dumps(listed)
        await revoke_invitation(db, p.plugin, p.installation, p.admin, {"id": str(second)})
    for _ in range(3):
        await issue(p)
    with pytest.raises(ValueError, match="limit"):
        await issue(p)
    async with SessionLocal() as db:
        assert all(
            len(key) == 64 and "@" not in key
            for key in await db.scalars(select(AuthRequestLimit.key))
        )


@pytest.mark.asyncio
async def test_withdrawal_restore_and_expired_cleanup(invitation):
    p = invitation
    identity, token = await issue(p)
    async with SessionLocal() as db:
        await unregister_invite_flow(db, p.plugin, p.installation, p.admin, {})
        result = await register_invite_flow(
            db,
            p.plugin,
            p.installation,
            p.admin,
            {"mode": "user_invite", "name": "Invites"},
            restore=True,
        )
        assert not result["registered"]
        await register_invite_flow(
            db, p.plugin, p.installation, p.admin, {"mode": "user_invite", "name": "Invites"}
        )
        assert not await accept_invitation(
            db, identity, token, "new-" + p.installation.hex, "New-Password-789!"
        )
        row = await db.get(UserInvitation, identity)
        pending = await db.get(NotificationPendingRecipient, row.pending_recipient_id)
        pending.expires_at = int(time.time()) - 86401
        await db.commit()
        await expire_invitations(db)
        assert (
            await db.get(NotificationPendingRecipient, pending.id, populate_existing=True) is None
        )


@pytest.mark.asyncio
async def test_admin_global_grant_and_payload_are_required(invitation):
    p = invitation
    assert not capability_implies("api.full", "auth.user_invites")
    assert capability_definition("auth.user_invites").risk.value == "critical"
    with pytest.raises(ValueError):
        await issue(p, is_admin=True)
    async with SessionLocal() as db:
        with pytest.raises(PermissionError):
            await dispatch_gateway_request(
                db,
                plugin_id=p.plugin,
                installation_id=p.installation,
                user_id=p.admin,
                method="auth_flows.user_invites.issue",
                capability="api.full",
                payload={"email": p.email},
            )
        grant = await db.get(PluginPermissionGrant, p.grant)
        grant.user_id = p.admin
        await db.commit()
        with pytest.raises(PermissionError):
            await issue_invitation(db, p.plugin, p.installation, p.admin, {"email": p.email})


@pytest.mark.asyncio
async def test_xor_database_owner_constraint(invitation):
    async with SessionLocal() as db:
        endpoint = NotificationDestination(
            provider_id="core.smtp", endpoint_key="invalid", kind="email"
        )
        db.add(endpoint)
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        pending = NotificationPendingRecipient(id=uuid4(), expires_at=int(time.time()) + 3600)
        db.add(pending)
        await db.flush()
        db.add(
            NotificationDestination(
                user_id=invitation.admin,
                pending_recipient_id=pending.id,
                provider_id="core.smtp",
                endpoint_key="invalid",
                kind="email",
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_smtp_failure_is_persisted_and_retry_uses_the_same_invitation(
    invitation, monkeypatch
):
    p = invitation
    identity, _ = await issue(p)
    original = smtp.send_mail

    def fail(*_args, **_kwargs):
        raise OSError("temporary local test failure")

    monkeypatch.setattr(smtp, "send_mail", fail)
    async with SessionLocal() as db:
        assert await process_pending_deliveries(db) == 0
        row = await db.get(UserInvitation, identity)
        delivery = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == row.notification_id
            )
        )
        assert delivery.status == "retry_wait" and delivery.attempts == 1
        assert await db.scalar(select(User).where(User.email == p.email)) is None
        delivery.next_attempt_at = 0
        await db.commit()
        monkeypatch.setattr(smtp, "send_mail", original)
        assert await process_pending_deliveries(db) == 1
        await db.refresh(delivery)
        assert delivery.status == "sent" and delivery.attempts == 2
        assert (
            len(
                list(
                    await db.scalars(
                        select(UserInvitation).where(UserInvitation.plugin_id == p.plugin)
                    )
                )
            )
            == 1
        )
    assert len(p.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["tls", "provider", "type", "trust"])
async def test_smtp_transport_and_administrative_restrictions_are_rechecked(
    invitation, monkeypatch, policy
):
    p = invitation
    await issue(p)
    if policy == "tls":
        monkeypatch.setenv("SMTP_HOST", "mail.public.example")
    elif policy == "provider":
        monkeypatch.setattr(settings, "NOTIFICATION_BLOCKED_PROVIDERS", "core.smtp")
    elif policy == "type":
        monkeypatch.setattr(settings, "NOTIFICATION_BLOCKED_TYPES", "auth.user_invite")
    else:
        monkeypatch.setattr(settings, "NOTIFICATION_MINIMUM_TRUST", 2)
    async with SessionLocal() as db:
        assert await process_pending_deliveries(db) == 0
    assert not p.messages


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawal", ["grant", "permissions", "disable", "uninstall"])
async def test_existing_host_withdrawal_hooks_retire_invites(invitation, monkeypatch, withdrawal):
    from src.api.routes.plugin_manager import lifecycle
    from src.api.routes.plugin_permissions import revoke_grant

    p = invitation
    identity, secret = await issue(p)
    monkeypatch.setattr(lifecycle.runtime.client, "stop", AsyncMock())
    async with SessionLocal() as db:
        admin = await db.get(User, p.admin)
        if withdrawal == "grant":
            await revoke_grant(p.grant, db, admin)
        elif withdrawal == "permissions":
            await lifecycle.revoke_plugin_permissions(p.plugin, db, admin)
        elif withdrawal == "disable":
            await lifecycle.disable_plugin(p.plugin, admin, db)
        else:
            await lifecycle._purge_plugin_database(db, p.plugin)
            await db.commit()
        row = await db.get(UserInvitation, identity)
        assert row.revoked_at and row.encrypted_token is None
        assert not await accept_invitation(
            db, identity, secret, "new-" + p.installation.hex, "New-Password-789!"
        )


@pytest.mark.asyncio
async def test_existing_email_or_username_conflict_never_creates_another_account(invitation):
    p = invitation
    async with SessionLocal() as db:
        admin = await db.get(User, p.admin)
        with pytest.raises(ValueError, match="already uses"):
            await issue_invitation(db, p.plugin, p.installation, p.admin, {"email": admin.email})
    identity, secret = await issue(p)
    async with SessionLocal() as db:
        admin = await db.get(User, p.admin)
        with pytest.raises(ValueError, match="already in use"):
            await accept_invitation(db, identity, secret, admin.username, "New-Password-789!")
        assert await db.scalar(select(User.id).where(User.email == p.email)) is None
