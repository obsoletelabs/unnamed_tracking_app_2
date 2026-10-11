"""Public reset behavior, reserved routing and single-use account mutation."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from src.api.routes.password_recovery import router
from src.core.auth import hash_password, verify_password
from src.core.crypto import decrypt_secret
from src.core.preferences import save_preferences
from src.database.models.auth import UserApiKey, UserSession
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.plugin_auth_flow import (
    AuthRecoveryToken,
    AuthRequestLimit,
    PluginAuthFlowRegistration,
)
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notification_enrollment import create_email, update_email
from src.features.notification_providers.delivery import process_pending_deliveries
from src.features.password_recovery import (
    complete_password_reset,
    expire_recovery_secrets,
    request_password_reset,
)
from src.features.plugin_auth_flows import (
    active_reset_flow,
    register_reset_flow,
    unregister_reset_flow,
)
from src.plugin_api.capabilities import capability_definition, capability_implies
from src.plugin_api.runtime_client import PluginRuntimeClient
from tests.test_notification_email import mailbox as mailbox
from tests.test_notification_email import verify


@pytest.fixture
async def recovery(mailbox, monkeypatch):
    user_id, installation, grant = uuid4(), uuid4(), uuid4()
    plugin = "test.reset." + installation.hex
    state = [
        {
            "plugin_id": plugin,
            "installation_id": str(installation),
            "enabled": True,
            "compatible": True,
            "status": "running",
            "health": "healthy",
            "api_contract_version": "1.1.7",
        }
    ]
    monkeypatch.setattr(PluginRuntimeClient, "plugins", AsyncMock(side_effect=lambda: state))
    async with SessionLocal() as db:
        await db.execute(delete(PluginAuthFlowRegistration))
        await db.execute(delete(AuthRequestLimit))
        db.add(
            User(
                id=user_id,
                username=user_id.hex,
                email=f"{user_id}@example.test",
                password_hash=hash_password("Original-Password-123!"),
                is_admin=True,
                last_app_url="https://library.example.test",
            )
        )
        db.add(
            PluginPermissionGrant(
                id=grant,
                plugin_id=plugin,
                installation_id=installation,
                capability="auth.password_reset",
                capability_version=1,
            )
        )
        await db.commit()
        endpoint = await create_email(db, user_id, f"{user_id}@example.test", "Recovery")
        await verify(db, user_id, endpoint, mailbox)
        await update_email(db, user_id, endpoint, {"recovery_allowed": True})
        await register_reset_flow(
            db, plugin, installation, user_id, {"mode": "password_reset", "name": "Password reset"}
        )
    mailbox.clear()
    yield SimpleNamespace(
        user=user_id,
        endpoint=endpoint,
        grant=grant,
        plugin=plugin,
        installation=installation,
        state=state,
        messages=mailbox,
    )
    async with SessionLocal() as db:
        await db.execute(delete(PluginAuthFlowRegistration))
        await db.execute(delete(AuthRequestLimit))
        await db.execute(delete(PluginPermissionGrant).where(PluginPermissionGrant.id == grant))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


async def issue(p):
    async with SessionLocal() as db:
        await request_password_reset(db, f"{p.user}@example.test", "test-caller")
        row = await db.scalar(
            select(AuthRecoveryToken)
            .where(AuthRecoveryToken.user_id == p.user)
            .order_by(AuthRecoveryToken.created_at.desc())
        )
        return (row.id, decrypt_secret(row.encrypted_token)) if row else (None, None)


def test_reset_permission_cannot_be_implied_by_broad_grants():
    for grant in ["api.full", "users", "notifications", "notifications.sensitive"]:
        assert not capability_implies(grant, "auth.password_reset")
    assert capability_definition("auth.password_reset").highly_privileged


@pytest.mark.asyncio
async def test_real_smtp_handoff_fragment_link_and_atomic_session_only_revocation(recovery):
    p = recovery
    identity, secret = await issue(p)
    async with SessionLocal() as db:
        token = await db.get(AuthRecoveryToken, identity)
        notice = await db.get(Notification, token.notification_id)
        assert notice.event_type == "auth.password_reset" and not notice.inbox_visible
        assert secret not in notice.body and secret not in token.token_hash
        assert token.encrypted_token != secret
        assert await process_pending_deliveries(db) == 1
    body = p.messages[-1].get_body(preferencelist=("plain",)).get_content()
    link = next(line for line in body.splitlines() if line.startswith("https://"))
    parsed = urlsplit(link)
    assert parsed.netloc == "library.example.test" and parsed.path == "/reset-password"
    assert not parsed.query and parse_qs(parsed.fragment)["token"] == [secret]
    session, key = uuid4(), uuid4()
    async with SessionLocal() as db:
        db.add(
            UserSession(
                id=session,
                user_id=p.user,
                token_hash=uuid4().hex,
                expires_at=int(time.time()) + 300,
            )
        )
        db.add(
            UserApiKey(id=key, user_id=p.user, name="Keep", key_prefix="key", key_hash=uuid4().hex)
        )
        await db.commit()
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Merely opening a link cannot mutate account state; no GET redemption route.
        assert (await client.get("/api/auth/password-reset/complete")).status_code == 405
        response = await client.post(
            "/api/auth/password-reset/complete",
            json={"id": str(identity), "token": secret, "password": "Replacement-Password-789!"},
        )
        assert response.status_code == 200 and not response.cookies
    async with SessionLocal() as db:
        assert verify_password(
            "Replacement-Password-789!", (await db.get(User, p.user)).password_hash
        )
        assert (await db.get(UserSession, session)).revoked_at is not None
        assert (await db.get(UserApiKey, key)).revoked_at is None
        assert (await db.get(AuthRecoveryToken, identity)).encrypted_token is None
        assert not await complete_password_reset(db, identity, secret, "Other-Password-123!")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["unverified", "no_recovery", "disabled", "inbox", "preference", "inactive", "sso", "grant"],
)
async def test_ineligible_accounts_destinations_never_get_recovery(recovery, change):
    p = recovery
    async with SessionLocal() as db:
        endpoint = await db.get(NotificationDestination, p.endpoint)
        if change == "unverified":
            endpoint.verified_revision = None
        elif change == "no_recovery":
            endpoint.recovery_allowed = False
        elif change == "disabled":
            endpoint.enabled = False
        elif change == "inbox":
            endpoint.channel_context = "internal"
        elif change == "preference":
            await save_preferences(
                db, p.user, {"notification_types": {"auth.password_reset": False}}
            )
        elif change == "inactive":
            (await db.get(User, p.user)).is_active = False
        elif change == "sso":
            (await db.get(User, p.user)).oidc_subject = "sso:" + p.user.hex
        elif change == "grant":
            (await db.get(PluginPermissionGrant, p.grant)).revoked_at = int(time.time())
        await db.commit()
    assert await issue(p) == (None, None)
    assert not p.messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["expiry", "grant", "install", "revision", "password", "email", "destination"]
)
async def test_late_changes_block_handoff_and_redemption(recovery, change):
    p = recovery
    identity, secret = await issue(p)
    async with SessionLocal() as db:
        row = await db.get(AuthRecoveryToken, identity)
        if change == "expiry":
            row.expires_at = int(time.time()) - 1
        elif change == "grant":
            (await db.get(PluginPermissionGrant, p.grant)).revoked_at = int(time.time())
        elif change == "install":
            p.state[0]["installation_id"] = str(uuid4())
        elif change == "revision":
            (await db.get(PluginAuthFlowRegistration, "password_reset")).revision += 1
        elif change == "password":
            (await db.get(User, p.user)).password_hash = hash_password("Changed-Password-456!")
        elif change == "email":
            (await db.get(User, p.user)).email = "changed-" + str(p.user) + "@example.test"
        elif change == "destination":
            (await db.get(NotificationDestination, p.endpoint)).recovery_allowed = False
        await db.commit()
        assert await process_pending_deliveries(db) == 0
        if change != "destination":
            assert not await complete_password_reset(
                db, identity, secret, "Replacement-Password-789!"
            )
    assert not p.messages


@pytest.mark.asyncio
async def test_wrong_token_password_policy_and_concurrent_single_use(recovery):
    identity, secret = await issue(recovery)
    async with SessionLocal() as db:
        assert not await complete_password_reset(
            db, identity, "x" * 43, "Replacement-Password-789!"
        )
        with pytest.raises(ValueError):
            await complete_password_reset(db, identity, secret, "short")

    async def redeem():
        async with SessionLocal() as db:
            return await complete_password_reset(db, identity, secret, "Replacement-Password-789!")

    assert sorted(await asyncio.gather(redeem(), redeem())) == [False, True]


@pytest.mark.asyncio
async def test_unknown_account_acknowledgement_limits_and_no_identifier_storage(recovery):
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        known = await client.post(
            "/api/auth/password-reset/request",
            json={"identifier": str(recovery.user) + "@example.test"},
        )
        missing = await client.post(
            "/api/auth/password-reset/request", json={"identifier": "unknown@example.test"}
        )
        assert known.status_code == missing.status_code == 200 and known.json() == missing.json()
    async with SessionLocal() as db:
        for _ in range(7):
            await request_password_reset(db, str(recovery.user) + "@example.test", "limit-caller")
        assert (
            len(
                list(
                    await db.scalars(
                        select(AuthRecoveryToken).where(AuthRecoveryToken.user_id == recovery.user)
                    )
                )
            )
            == 5
        )
        assert all(
            len(key) == 64 and "@" not in key
            for key in await db.scalars(select(AuthRequestLimit.key))
        )


@pytest.mark.asyncio
async def test_registration_requires_admin_global_explicit_grant_and_expired_secrets_clear(
    recovery,
):
    p = recovery
    async with SessionLocal() as db:
        grant = await db.get(PluginPermissionGrant, p.grant)
        grant.user_id = p.user
        await db.commit()
        assert await active_reset_flow(db) is None
        with pytest.raises(PermissionError):
            await register_reset_flow(
                db, p.plugin, p.installation, p.user, {"mode": "password_reset", "name": "Reset"}
            )
        grant.user_id = None
        await db.commit()
    identity, _ = await issue(p)
    async with SessionLocal() as db:
        row = await db.get(AuthRecoveryToken, identity)
        row.expires_at = int(time.time()) - 1
        await db.commit()
        await expire_recovery_secrets(db)
        await db.commit()
        await db.refresh(row)
        assert row.encrypted_token is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "withdrawal", ["grant", "permissions", "disable", "unregister", "uninstall"]
)
async def test_withdrawal_and_reactivation_never_revive_reset_credentials(
    recovery, withdrawal, monkeypatch
):
    from src.api.routes.plugin_manager import lifecycle
    from src.api.routes.plugin_permissions import revoke_grant

    p = recovery
    identity, secret = await issue(p)
    monkeypatch.setattr(lifecycle.runtime.client, "stop", AsyncMock())
    async with SessionLocal() as db:
        admin = await db.get(User, p.user)
        if withdrawal == "grant":
            await revoke_grant(p.grant, db, admin)
        elif withdrawal == "permissions":
            await lifecycle.revoke_plugin_permissions(p.plugin, db, admin)
        elif withdrawal == "disable":
            await lifecycle.disable_plugin(p.plugin, admin, db)
        elif withdrawal == "uninstall":
            await lifecycle._purge_plugin_database(db, p.plugin)
            await db.commit()
        else:
            await unregister_reset_flow(db, p.plugin, p.installation, p.user, {})
        record = await db.get(AuthRecoveryToken, identity)
        await db.refresh(record)
        assert record.used_at is not None and record.encrypted_token is None
        delivery = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == record.notification_id
            )
        )
        assert delivery.status == "suppressed" and delivery.claim_token is None
        grant = await db.get(PluginPermissionGrant, p.grant)
        if grant is None:
            db.add(
                PluginPermissionGrant(
                    id=p.grant,
                    plugin_id=p.plugin,
                    installation_id=p.installation,
                    capability="auth.password_reset",
                    capability_version=1,
                )
            )
        else:
            grant.revoked_at = None
        await db.commit()
        restored = await register_reset_flow(
            db,
            p.plugin,
            p.installation,
            p.user,
            {"mode": "password_reset", "name": "Password reset"},
            restore=True,
        )
        assert restored["registered"] is False
        await register_reset_flow(
            db, p.plugin, p.installation, p.user, {"mode": "password_reset", "name": "Reset"}
        )
        assert await active_reset_flow(db) is not None
        assert not await complete_password_reset(db, identity, secret, "Replacement-Password-789!")
        assert await process_pending_deliveries(db) == 0
    assert not p.messages


@pytest.mark.asyncio
async def test_different_tokens_for_one_account_have_one_concurrent_winner(recovery):
    p = recovery
    async with SessionLocal() as db:
        for _ in range(2):
            await request_password_reset(db, str(p.user) + "@example.test", "parallel-caller")
        credentials = [
            (row.id, decrypt_secret(row.encrypted_token))
            for row in await db.scalars(
                select(AuthRecoveryToken).where(AuthRecoveryToken.user_id == p.user)
            )
        ]
    assert len(credentials) == 2

    async def redeem(identity, secret):
        async with SessionLocal() as db:
            return await complete_password_reset(db, identity, secret, "Replacement-Password-789!")

    assert sorted(await asyncio.gather(*(redeem(*item) for item in credentials))) == [False, True]
