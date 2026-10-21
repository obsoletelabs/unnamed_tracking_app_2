"""Audited administrator account editing over the real auth, recovery and invitation flows."""

import re
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.admin_users import router
from src.api.routes.auth import router as auth_router
from src.core.auth import get_current_admin, hash_password, verify_password
from src.core.config import settings
from src.database.models.admin_user_audit import AdminUserAudit
from src.database.models.auth import UserSession
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_verification import NotificationVerification
from src.database.models.plugin_auth_flow import AuthRecoveryToken, AuthRequestLimit
from src.database.models.user import User
from src.database.models.user_invitation import UserInvitation
from src.database.session import SessionLocal, get_db
from src.features.notification_auth_secrets import account_fingerprint
from src.features.notification_enrollment import (
    confirm_verification,
    update_email,
)
from src.features.notification_providers.delivery import process_pending_deliveries
from src.features.password_recovery import complete_password_reset
from tests.test_notification_email import mailbox as mailbox
from tests.test_password_recovery import recovery as recovery
from tests.test_user_invitations import invitation as invitation
from tests.test_user_invitations import issue as issue_invite

ADMIN_PASSWORD = "Admin-Password-123!"
NEW_PASSWORD = "Replacement-Password-789!"


@asynccontextmanager
async def api(admin_id=None, username=None):
    """Client acting as the given administrator (dependency stands in for the session)."""
    app = FastAPI()
    app.include_router(router)
    app.include_router(auth_router)
    if admin_id is not None:
        # Same shape as the real dependency: the admin row lives in the route's own session.
        async def current_admin(db: AsyncSession = Depends(get_db)) -> User:
            return await db.get(User, admin_id)

        app.dependency_overrides[get_current_admin] = current_admin
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
async def accounts():
    admin_id, target_id = uuid4(), uuid4()
    async with SessionLocal() as db:
        await db.execute(delete(AuthRequestLimit))
        db.add_all(
            [
                User(
                    id=admin_id,
                    username="adm" + admin_id.hex[:12],
                    email=f"{admin_id}@example.test",
                    password_hash=hash_password(ADMIN_PASSWORD),
                    is_admin=True,
                    last_app_url="https://library.example.test",
                ),
                User(
                    id=target_id,
                    username="tgt" + target_id.hex[:12],
                    email=f"{target_id}@example.test",
                    password_hash=hash_password("Original-Password-123!"),
                    last_app_url="https://library.example.test",
                ),
            ]
        )
        await db.commit()
    yield SimpleNamespace(
        admin=admin_id,
        admin_name="adm" + admin_id.hex[:12],
        target=target_id,
        target_name="tgt" + target_id.hex[:12],
        email=f"{target_id}@example.test",
    )
    async with SessionLocal() as db:
        await db.execute(delete(AdminUserAudit).where(AdminUserAudit.actor_id == admin_id))
        await db.execute(delete(AuthRequestLimit))
        await db.execute(delete(User).where(User.id.in_([admin_id, target_id])))
        await db.commit()


def sensitive(a, **extra):
    return {"admin_password": ADMIN_PASSWORD, "confirm_username": a.target_name, **extra}


async def audit_rows(admin_id):
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(AdminUserAudit)
                .where(AdminUserAudit.actor_id == admin_id)
                .order_by(AdminUserAudit.occurred_at, AdminUserAudit.id)
            )
        )


async def add_session(user_id):
    async with SessionLocal() as db:
        row = UserSession(
            id=uuid4(), user_id=user_id, token_hash=uuid4().hex, expires_at=int(time.time()) + 600
        )
        db.add(row)
        await db.commit()
        return row.id


@pytest.mark.asyncio
async def test_endpoints_require_an_administrator(accounts):
    a = accounts
    async with api() as client:
        for method, path in (
            ("post", f"/api/auth/users/{a.target}/email"),
            ("post", f"/api/auth/users/{a.target}/password"),
            ("post", f"/api/auth/users/{a.target}/password-reset-link"),
            ("post", f"/api/auth/users/{a.target}/force-password-reset"),
            ("post", f"/api/auth/users/{a.target}/email-verification"),
            ("get", f"/api/auth/users/{a.target}/email-destinations"),
            ("get", f"/api/auth/user-audit/{a.target}"),
            ("get", "/api/auth/user-invitations"),
            ("post", f"/api/auth/user-invitations/{uuid4()}/resend"),
        ):
            assert (await client.request(method.upper(), path)).status_code in (401, 403)


@pytest.mark.asyncio
async def test_change_email_needs_confirmation_and_admin_proof_and_is_audited(accounts):
    a = accounts
    path = f"/api/auth/users/{a.target}/email"
    async with api(a.admin, a.admin_name) as client:
        body = {**sensitive(a), "email": "renamed@example.test"}
        wrong_confirm = await client.post(path, json={**body, "confirm_username": "someone"})
        assert wrong_confirm.status_code == 400
        wrong_password = await client.post(path, json={**body, "admin_password": "Nope-123456!"})
        assert wrong_password.status_code == 403
        assert (await client.post(path, json={**body, "email": "bad address"})).status_code == 400
        async with SessionLocal() as db:
            assert (await db.get(User, a.target)).email == a.email
        ok = await client.post(path, json=body)
        assert ok.status_code == 200 and ok.json()["email"] == "renamed@example.test"
        again = await client.post(path, json=body)
        assert again.status_code == 400
        clash = await client.post(path, json={**body, "email": f"{a.admin}@example.test"})
        assert clash.status_code == 409
        history = (await client.get(f"/api/auth/user-audit/{a.target}")).json()
    async with SessionLocal() as db:
        assert (await db.get(User, a.target)).email == "renamed@example.test"
    outcomes = [(row.action, row.outcome) for row in await audit_rows(a.admin)]
    assert ("email.change", "denied") in outcomes and ("email.change", "success") in outcomes
    success = next(row for row in await audit_rows(a.admin) if row.outcome == "success")
    assert "r***@example.test" in success.detail and "renamed@" not in success.detail
    assert {entry["outcome"] for entry in history} == {"denied", "success"}
    assert ADMIN_PASSWORD not in str(history)


@pytest.mark.asyncio
async def test_email_change_voids_outstanding_reset_links(accounts):
    a = accounts
    async with SessionLocal() as db:
        before = account_fingerprint(await db.get(User, a.target))
    async with api(a.admin, a.admin_name) as client:
        await client.post(
            f"/api/auth/users/{a.target}/email",
            json={**sensitive(a), "email": "moved@example.test"},
        )
    async with SessionLocal() as db:
        assert account_fingerprint(await db.get(User, a.target)) != before


@pytest.mark.asyncio
async def test_set_password_revokes_sessions_and_never_echoes_secrets(accounts):
    a = accounts
    session = await add_session(a.target)
    path = f"/api/auth/users/{a.target}/password"
    async with api(a.admin, a.admin_name) as client:
        weak = await client.post(path, json={**sensitive(a), "new_password": "weak"})
        assert weak.status_code == 422
        bad = await client.post(
            path, json={**sensitive(a, admin_password="Nope-123456!"), "new_password": NEW_PASSWORD}
        )
        assert bad.status_code == 403
        ok = await client.post(path, json={**sensitive(a), "new_password": NEW_PASSWORD})
    assert ok.status_code == 200 and NEW_PASSWORD not in ok.text
    assert ok.json() == {"status": "password_changed", "sessions_revoked": 1}
    async with SessionLocal() as db:
        stored = (await db.get(User, a.target)).password_hash
        assert verify_password(NEW_PASSWORD, stored) and stored.startswith("argon2id$")
        assert (await db.get(UserSession, session)).revoked_at is not None
    rows = await audit_rows(a.admin)
    assert NEW_PASSWORD not in " ".join(row.detail for row in rows)
    assert ("password.set", "success") in [(row.action, row.outcome) for row in rows]


@pytest.mark.asyncio
async def test_credential_changes_refuse_self_primary_and_sso_accounts(accounts):
    a = accounts
    async with SessionLocal() as db:
        db.add(
            User(
                id=(sso := uuid4()),
                username="sso" + sso.hex[:12],
                email=f"{sso}@example.test",
                password_hash=hash_password("x"),
                oidc_subject="subject-" + sso.hex,
            )
        )
        primary = User(
            id=(pid := uuid4()),
            username="primary-" + uuid4().hex[:8],
            email=f"{pid}@example.test",
            password_hash=hash_password("x"),
        )
        db.add(primary)
        await db.commit()
    try:
        async with api(a.admin, a.admin_name) as client:
            own = await client.post(
                f"/api/auth/users/{a.admin}/password",
                json={
                    "admin_password": ADMIN_PASSWORD,
                    "confirm_username": a.admin_name,
                    "new_password": NEW_PASSWORD,
                },
            )
            assert own.status_code == 400
            sso_reply = await client.post(
                f"/api/auth/users/{sso}/password",
                json={
                    "admin_password": ADMIN_PASSWORD,
                    "confirm_username": "sso" + sso.hex[:12],
                    "new_password": NEW_PASSWORD,
                },
            )
            assert sso_reply.status_code == 409
            settings.PRIMARY_USER_USERNAME = primary.username
            try:
                blocked = await client.post(
                    f"/api/auth/users/{pid}/password",
                    json={
                        "admin_password": ADMIN_PASSWORD,
                        "confirm_username": primary.username,
                        "new_password": NEW_PASSWORD,
                    },
                )
            finally:
                settings.PRIMARY_USER_USERNAME = "test"
            assert blocked.status_code == 403
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id.in_([sso, pid])))
            await db.commit()


@pytest.mark.asyncio
async def test_role_changes_and_deletion_are_audited(accounts):
    a = accounts
    async with api(a.admin, a.admin_name) as client:
        promoted = await client.patch(f"/api/auth/users/{a.target}", json={"is_admin": True})
        assert promoted.status_code == 200
    assert ("role.promote", "success") in [(r.action, r.outcome) for r in await audit_rows(a.admin)]


@pytest.mark.asyncio
async def test_reset_link_uses_verified_recovery_destination_only(recovery):
    p = recovery
    async with SessionLocal() as db:
        # The fixture user is an administrator; use a second admin as the actor.
        actor = uuid4()
        db.add(
            User(
                id=actor,
                username="act" + actor.hex[:12],
                email=f"{actor}@example.test",
                password_hash=hash_password(ADMIN_PASSWORD),
                is_admin=True,
            )
        )
        await db.commit()
    try:
        async with api(actor, "act" + actor.hex[:12]) as client:
            queued = await client.post(f"/api/auth/users/{p.user}/password-reset-link")
            assert queued.status_code == 200 and queued.json()["password_voided"] is False
            async with SessionLocal() as db:
                assert await process_pending_deliveries(db) == 1
                user = await db.get(User, p.user)
                assert verify_password("Original-Password-123!", user.password_hash)
            body = p.messages[-1].get_body(preferencelist=("plain",)).get_content()
            assert "/reset-password#" in body
            # Without recovery routing nothing is sent and no credential is retained.
            async with SessionLocal() as db:
                await update_email(db, p.user, p.endpoint, {"recovery_allowed": False})
                await db.execute(delete(AuthRequestLimit))
                await db.commit()
            refused = await client.post(f"/api/auth/users/{p.user}/password-reset-link")
            assert refused.status_code == 409 and "verified" in refused.json()["detail"]
            async with SessionLocal() as db:
                live = list(
                    await db.scalars(
                        select(AuthRecoveryToken).where(
                            AuthRecoveryToken.user_id == p.user,
                            AuthRecoveryToken.used_at.is_(None),
                        )
                    )
                )
                assert len(live) == 1
        outcomes = [(r.action, r.outcome) for r in await audit_rows(actor)]
        assert ("password.reset_link", "success") in outcomes
        assert ("password.reset_link", "refused") in outcomes
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(AdminUserAudit).where(AdminUserAudit.actor_id == actor))
            await db.execute(delete(User).where(User.id == actor))
            await db.commit()


@pytest.mark.asyncio
async def test_force_reset_voids_password_and_sessions_then_user_completes_reset(recovery):
    p = recovery
    actor = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=actor,
                username="act" + actor.hex[:12],
                email=f"{actor}@example.test",
                password_hash=hash_password(ADMIN_PASSWORD),
                is_admin=True,
            )
        )
        target = await db.get(User, p.user)
        name = target.username
        await db.commit()
    session = await add_session(p.user)
    payload = {"admin_password": ADMIN_PASSWORD, "confirm_username": name}
    try:
        async with api(actor, "act" + actor.hex[:12]) as client:
            path = f"/api/auth/users/{p.user}/force-password-reset"
            assert (
                await client.post(path, json={**payload, "confirm_username": "x"})
            ).status_code == 400
            denied = await client.post(path, json={**payload, "admin_password": "Nope-123456!"})
            assert denied.status_code == 403
            async with SessionLocal() as db:
                assert verify_password(
                    "Original-Password-123!", (await db.get(User, p.user)).password_hash
                )
            ok = await client.post(path, json=payload)
            assert ok.status_code == 200 and ok.json()["password_voided"] is True
        async with SessionLocal() as db:
            user = await db.get(User, p.user)
            assert not verify_password("Original-Password-123!", user.password_hash)
            assert (await db.get(UserSession, session)).revoked_at is not None
            assert await process_pending_deliveries(db) == 1
        link = next(
            line
            for line in p.messages[-1]
            .get_body(preferencelist=("plain",))
            .get_content()
            .splitlines()
            if "/reset-password#" in line
        )
        fragment = link.split("#", 1)[1]
        token_id = re.search(r"id=([0-9a-f-]+)", fragment).group(1)
        secret = re.search(r"token=([^&]+)", fragment).group(1)
        async with SessionLocal() as db:
            assert await complete_password_reset(db, token_id_uuid(token_id), secret, NEW_PASSWORD)
            assert verify_password(NEW_PASSWORD, (await db.get(User, p.user)).password_hash)
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(AdminUserAudit).where(AdminUserAudit.actor_id == actor))
            await db.execute(delete(User).where(User.id == actor))
            await db.commit()


def token_id_uuid(value):
    return UUID(value)


@pytest.mark.asyncio
async def test_force_reset_without_verified_destination_changes_nothing(recovery):
    p = recovery
    actor = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=actor,
                username="act" + actor.hex[:12],
                email=f"{actor}@example.test",
                password_hash=hash_password(ADMIN_PASSWORD),
                is_admin=True,
            )
        )
        await update_email(db, p.user, p.endpoint, {"recovery_allowed": False})
        name = (await db.get(User, p.user)).username
        await db.commit()
    session = await add_session(p.user)
    try:
        async with api(actor, "act" + actor.hex[:12]) as client:
            reply = await client.post(
                f"/api/auth/users/{p.user}/force-password-reset",
                json={"admin_password": ADMIN_PASSWORD, "confirm_username": name},
            )
        assert reply.status_code == 409
        async with SessionLocal() as db:
            assert verify_password(
                "Original-Password-123!", (await db.get(User, p.user)).password_hash
            )
            assert (await db.get(UserSession, session)).revoked_at is None
            assert not list(
                await db.scalars(
                    select(Notification).where(
                        Notification.user_id == p.user, Notification.kind == "password_reset"
                    )
                )
            )
        assert ("password.force_reset", "refused") in [
            (r.action, r.outcome) for r in await audit_rows(actor)
        ]
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(AdminUserAudit).where(AdminUserAudit.actor_id == actor))
            await db.execute(delete(User).where(User.id == actor))
            await db.commit()


@pytest.mark.asyncio
async def test_verification_is_proved_by_the_mailbox_never_forced(accounts, mailbox):
    a = accounts
    async with api(a.admin, a.admin_name) as client:
        sent = await client.post(f"/api/auth/users/{a.target}/email-verification", json={})
        assert sent.status_code == 200
        listed = (await client.get(f"/api/auth/users/{a.target}/email-destinations")).json()
        assert len(listed) == 1 and listed[0]["is_account_email"] and not listed[0]["verified"]
        assert a.email not in str(listed) and listed[0]["address"].endswith("@example.test")
        async with SessionLocal() as db:
            assert await process_pending_deliveries(db) == 1
        assert mailbox[-1]["To"] == a.email
        code = re.search(
            r"code is ([0-9]{8})", mailbox[-1].get_body(preferencelist=("plain",)).get_content()
        ).group(1)
        # Requesting again re-uses the same destination (no duplicates), subject to throttling.
        again = await client.post(f"/api/auth/users/{a.target}/email-verification", json={})
        assert again.status_code == 429
        destination = listed[0]["id"]
        async with SessionLocal() as db:
            row = await db.get(NotificationDestination, UUID(destination))
            assert row.verified_revision is None
        revoke_wrong = await client.post(
            f"/api/auth/users/{a.target}/email-verification/revoke",
            json={"destination_id": destination, "confirm_username": "x"},
        )
        assert revoke_wrong.status_code == 400
    async with SessionLocal() as db:
        challenge = await db.scalar(
            select(NotificationVerification.id).where(NotificationVerification.user_id == a.target)
        )
        assert await confirm_verification(db, a.target, UUID(destination), challenge, code)
    async with api(a.admin, a.admin_name) as client:
        listed = (await client.get(f"/api/auth/users/{a.target}/email-destinations")).json()
        assert listed[0]["verified"]
        revoked = await client.post(
            f"/api/auth/users/{a.target}/email-verification/revoke",
            json={"destination_id": destination, "confirm_username": a.target_name},
        )
        assert revoked.status_code == 200
        listed = (await client.get(f"/api/auth/users/{a.target}/email-destinations")).json()
        assert not listed[0]["verified"] and not listed[0]["recovery_allowed"]
    actions = [(r.action, r.outcome) for r in await audit_rows(a.admin)]
    assert ("email.verification_request", "success") in actions
    assert ("email.verification_revoke", "success") in actions


@pytest.mark.asyncio
async def test_invited_accounts_can_be_resent_or_withdrawn_but_not_after_acceptance(invitation):
    p = invitation
    identity, first_token = await issue_invite(p)
    async with api(p.admin, "adm") as client:
        listing = (await client.get("/api/auth/user-invitations")).json()
        mine = next(row for row in listing if row["id"] == str(identity))
        assert mine["status"] == "pending" and mine["email"] == p.email
        assert "token" not in str(mine)
        resent = await client.post(f"/api/auth/user-invitations/{identity}/resend")
        assert resent.status_code == 200 and resent.json()["id"] != str(identity)
        new_id = resent.json()["id"]
        async with SessionLocal() as db:
            old = await db.get(UserInvitation, identity)
            assert old.revoked_at is not None and old.encrypted_token is None
            assert await process_pending_deliveries(db) == 1
        assert p.messages[-1]["To"] == p.email
        text = p.messages[-1].get_body(preferencelist=("plain",)).get_content()
        assert first_token not in text and "/accept-invite#" in text
        assert (
            await client.post(f"/api/auth/user-invitations/{uuid4()}/resend")
        ).status_code == 404
        withdrawn = await client.delete(f"/api/auth/user-invitations/{new_id}")
        assert withdrawn.status_code == 200
        listing = (await client.get("/api/auth/user-invitations")).json()
        assert next(row for row in listing if row["id"] == new_id)["status"] == "revoked"
        # Accepted invitations are active accounts and cannot be resent.
        async with SessionLocal() as db:
            row = await db.get(UserInvitation, identity)
            row.used_at = int(time.time())
            await db.commit()
        refused = await client.post(f"/api/auth/user-invitations/{identity}/resend")
        assert refused.status_code == 409 and "active" in refused.json()["detail"]
        history = (await client.get(f"/api/auth/user-audit/{identity}")).json()
    assert {entry["outcome"] for entry in history} == {"refused", "success"}
    async with SessionLocal() as db:
        await db.execute(delete(AdminUserAudit).where(AdminUserAudit.actor_id == p.admin))
        await db.commit()


@pytest.mark.asyncio
async def test_resend_requires_an_active_invitation_flow(invitation):
    p = invitation
    identity, _ = await issue_invite(p)
    p.state[0]["enabled"] = False
    async with api(p.admin, "adm") as client:
        reply = await client.post(f"/api/auth/user-invitations/{identity}/resend")
    assert reply.status_code == 409
    async with SessionLocal() as db:
        await db.execute(delete(AdminUserAudit).where(AdminUserAudit.actor_id == p.admin))
        await db.commit()


@pytest.mark.asyncio
async def test_verification_without_smtp_is_a_clear_refusal_that_is_audited(accounts, monkeypatch):
    a = accounts
    monkeypatch.setenv("SMTP_HOST", "")
    async with api(a.admin, a.admin_name) as client:
        reply = await client.post(f"/api/auth/users/{a.target}/email-verification", json={})
        assert reply.status_code == 400 and "SMTP" in reply.json()["detail"]
        history = (await client.get(f"/api/auth/user-audit/{a.target}")).json()
    assert [(e["action"], e["outcome"]) for e in history] == [
        ("email.verification_request", "refused")
    ]
