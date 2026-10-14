"""Account security through HTTP, real authentication and persisted PostgreSQL rows."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from src.api.routes import auth, setup
from src.core.auth import hash_password, hash_token, session_cookie_name, verify_password
from src.core.env_handler import EnvConfigHandler
from src.database.models.auth import UserApiKey, UserSession
from src.database.models.user import User
from src.database.session import SessionLocal, engine

PASSWORD = "Original-Password-123!"
NEW_PASSWORD = "Changed-Password-456!"


@pytest.fixture
async def accounts(monkeypatch):
    async with engine.begin() as connection:
        for table in (User.__table__, UserSession.__table__, UserApiKey.__table__):
            await connection.run_sync(lambda conn, table=table: table.create(conn, checkfirst=True))
    nonce = uuid4().hex
    users = [
        User(
            id=uuid4(),
            username=f"{name}-{nonce}",
            email=f"{name}-{nonce}@example.test",
            password_hash=hash_password(PASSWORD),
            is_admin=name == "admin",
            is_active=True,
        )
        for name in ("member", "admin")
    ]
    now = int(time.time())
    tokens = [f"member-cookie-{nonce}", f"admin-cookie-{nonce}", f"other-cookie-{nonce}"]
    sessions = [
        UserSession(
            user_id=users[index if index < 2 else 0].id,
            token_hash=hash_token(token),
            expires_at=now + 3600,
            created_at=now,
            last_seen_at=now,
        )
        for index, token in enumerate(tokens)
    ]
    api_key = f"utk_test-{nonce}"
    key = UserApiKey(
        user_id=users[0].id,
        name="Existing key",
        key_prefix=api_key[:12],
        key_hash=hash_token(api_key),
        scopes=[],
    )
    async with SessionLocal() as db:
        db.add_all(users)
        await db.flush()
        db.add_all([*sessions, key])
        await db.commit()
    monkeypatch.setattr(auth, "profile_picture_version", AsyncMock(return_value=None))
    monkeypatch.setattr(setup, "_configuration", AsyncMock(return_value={"sections": []}))
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(setup.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        state = SimpleNamespace(
            client=client,
            app=app,
            users=users,
            tokens=tokens,
            sessions=sessions,
            api_key=api_key,
            ids=[user.id for user in users],
        )
        yield state
    async with SessionLocal() as db:
        await db.execute(delete(UserApiKey).where(UserApiKey.user_id.in_(state.ids)))
        await db.execute(delete(UserSession).where(UserSession.user_id.in_(state.ids)))
        await db.execute(delete(User).where(User.id.in_(state.ids)))
        await db.commit()


def sign_in(accounts, index=0):
    accounts.client.cookies.set(session_cookie_name("test"), accounts.tokens[index])


async def test_login_prefers_exact_legacy_username_over_another_accounts_email(
    accounts, monkeypatch
):
    collision = f"collision-{uuid4().hex}@example.test"
    email_owner = User(
        id=uuid4(),
        username=f"owner-{uuid4().hex}",
        email=collision,
        password_hash=hash_password("Other-Password-789!"),
        is_active=True,
    )
    legacy = User(
        id=uuid4(),
        username=collision,
        email=f"legacy-{uuid4().hex}@example.test",
        password_hash=hash_password(PASSWORD),
        is_active=True,
    )
    accounts.ids.extend([email_owner.id, legacy.id])
    async with SessionLocal() as db:
        db.add_all([email_owner, legacy])
        await db.commit()
    monkeypatch.setattr(
        auth, "create_session", AsyncMock(return_value=SimpleNamespace(token="new"))
    )
    response = await accounts.client.post(
        "/api/auth/login", json={"username_or_email": collision, "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == str(legacy.id)
    # An exact username's wrong password must not fall through to a different email owner.
    response = await accounts.client.post(
        "/api/auth/login", json={"username_or_email": collision, "password": "Other-Password-789!"}
    )
    assert response.status_code == 401


async def test_login_email_fallback_is_case_insensitive_and_parameterized(accounts, monkeypatch):
    monkeypatch.setattr(
        auth, "create_session", AsyncMock(return_value=SimpleNamespace(token="new"))
    )
    response = await accounts.client.post(
        "/api/auth/login",
        json={"username_or_email": accounts.users[0].email.upper(), "password": PASSWORD},
    )
    assert response.status_code == 200
    for identifier in (
        "' OR 1=1 -- " + uuid4().hex,
        "SELECT * FROM users WHERE admin=" + uuid4().hex,
    ):
        response = await accounts.client.post(
            "/api/auth/login", json={"username_or_email": identifier, "password": PASSWORD}
        )
        assert response.status_code == 401


@pytest.mark.parametrize(
    "username",
    [
        "name with spaces",
        "email@example.test",
        "SELECT * FROM users WHERE admin",
        "x' OR 1=1--",
        "../name",
        "José",
        "name\nother",
        "name/other",
        "name;DROP",
    ],
)
async def test_admin_creation_rejects_unsupported_username_characters(accounts, username):
    sign_in(accounts, 1)
    response = await accounts.client.post(
        "/api/auth/users",
        json={
            "username": username,
            "email": f"new-{uuid4().hex}@example.test",
            "password": PASSWORD,
        },
    )
    if response.status_code == 201:
        accounts.ids.append(UUID(response.json()["id"]))
    assert response.status_code == 422
    assert "ASCII" in response.text


async def test_allowed_username_punctuation_and_legacy_profile_preservation(accounts):
    sign_in(accounts)
    name = "Alice_9-" + uuid4().hex + ".test"
    response = await accounts.client.patch("/api/auth/me", json={"username": name})
    assert response.status_code == 200 and response.json()["username"] == name
    legacy_name = "Legacy Name " + uuid4().hex
    async with SessionLocal() as db:
        user = await db.get(User, accounts.users[0].id)
        user.username = legacy_name
        await db.commit()
    response = await accounts.client.patch("/api/auth/me", json={"username": legacy_name})
    assert response.status_code == 200 and response.json()["username"] == legacy_name
    response = await accounts.client.patch("/api/auth/me", json={"username": "New Invalid Name"})
    assert response.status_code == 400 and "ASCII" in response.text


async def test_password_change_revokes_other_sessions_but_preserves_current_and_foreign(accounts):
    sign_in(accounts)
    response = await accounts.client.patch(
        "/api/auth/me",
        json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
        },
    )
    assert response.status_code == 200
    async with SessionLocal() as db:
        rows = (
            await db.scalars(select(UserSession).where(UserSession.user_id.in_(accounts.ids)))
        ).all()
        states = {row.token_hash: row.revoked_at for row in rows}
        assert states[hash_token(accounts.tokens[0])] is None
        assert states[hash_token(accounts.tokens[1])] is None
        assert states[hash_token(accounts.tokens[2])] is not None
        user = await db.get(User, accounts.users[0].id)
        assert verify_password(NEW_PASSWORD, user.password_hash)
    accounts.client.cookies.set(session_cookie_name("test"), accounts.tokens[2])
    assert (await accounts.client.patch("/api/auth/me", json={})).status_code == 401
    sign_in(accounts)
    assert (await accounts.client.patch("/api/auth/me", json={})).status_code == 200


async def test_failed_profile_transaction_does_not_revoke_sessions(accounts):
    sign_in(accounts)
    response = await accounts.client.patch(
        "/api/auth/me",
        json={
            "username": accounts.users[1].username,
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
        },
    )
    assert response.status_code == 409
    async with SessionLocal() as db:
        rows = (
            await db.scalars(select(UserSession).where(UserSession.user_id == accounts.users[0].id))
        ).all()
        assert all(row.revoked_at is None for row in rows)
        user = await db.get(User, accounts.users[0].id)
        assert verify_password(PASSWORD, user.password_hash)


async def test_api_key_cannot_mint_keys_even_with_a_browser_cookie(accounts):
    sign_in(accounts)
    response = await accounts.client.post(
        "/api/auth/api-keys",
        json={"name": "Child"},
        headers={"Authorization": f"Bearer {accounts.api_key}"},
    )
    assert response.status_code == 403
    async with SessionLocal() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(UserApiKey)
                .where(UserApiKey.user_id == accounts.users[0].id)
            )
            == 1
        )
    response = await accounts.client.post("/api/auth/api-keys", json={"name": "Browser"})
    assert response.status_code == 200 and response.json()["api_key"].startswith("utk_")


async def test_setup_configuration_requires_admin_after_bootstrap(accounts):
    assert (await accounts.client.get("/api/setup/configuration")).status_code == 401
    sign_in(accounts)
    assert (await accounts.client.get("/api/setup/configuration")).status_code == 403
    sign_in(accounts, 1)
    assert (await accounts.client.get("/api/setup/configuration")).status_code == 200


def test_env_only_database_url_is_redacted_in_setup_schema():
    url = "postgresql+asyncpg://user:do-not-expose@example.test/database"
    handler = EnvConfigHandler({"DATABASE_URL": url})
    field = next(
        field
        for section in handler.setup_schema()
        for field in section["fields"]
        if field["name"] == "DATABASE_URL"
    )
    assert field["secret"] and field["configured"] and field["env_only"]
    assert field["value"] is None and url not in str(handler.setup_schema())
    assert handler.get("DATABASE_URL") == url


async def test_first_run_configuration_remains_public(accounts):
    app = accounts.app
    app.dependency_overrides[setup.get_db] = lambda: SimpleNamespace(
        scalar=AsyncMock(return_value=None)
    )
    response = await accounts.client.get("/api/setup/configuration")
    assert response.status_code == 200 and response.json() == {"sections": []}


async def test_api_key_password_change_does_not_preserve_an_ambient_browser_cookie(accounts):
    sign_in(accounts)
    response = await accounts.client.patch(
        "/api/auth/me",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {accounts.api_key}"},
    )
    assert response.status_code == 200
    async with SessionLocal() as db:
        rows = (
            await db.scalars(select(UserSession).where(UserSession.user_id == accounts.users[0].id))
        ).all()
        assert all(row.revoked_at is not None for row in rows)


async def test_invitation_creation_uses_the_same_username_rules():
    from src.features.user_invitations import accept_invitation

    with pytest.raises(ValueError, match="ASCII"):
        await accept_invitation(SimpleNamespace(), uuid4(), "token", "name with spaces", PASSWORD)


def test_oidc_generated_username_sanitizes_both_claim_and_email_fallback():
    from src.api.routes.auth_oidc import _safe_username

    assert _safe_username("José", "unused@example.test") == "Jos"
    assert _safe_username("猫", "name+tag@example.test") == "nametag"


async def test_password_change_cannot_leave_a_concurrent_old_password_sign_in_active(
    accounts, monkeypatch
):
    authenticated, release = asyncio.Event(), asyncio.Event()
    token = "concurrent-" + uuid4().hex

    async def held_session(db, user, _request):
        authenticated.set()
        await release.wait()
        now = int(time.time())
        db.add(
            UserSession(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=now + 3600,
                created_at=now,
                last_seen_at=now,
            )
        )
        await db.flush()
        return SimpleNamespace(token=token)

    monkeypatch.setattr(auth, "create_session", held_session)
    sign_in(accounts)
    login_task = asyncio.create_task(
        accounts.client.post(
            "/api/auth/login",
            json={
                "username_or_email": accounts.users[0].username,
                "password": PASSWORD,
            },
        )
    )
    await asyncio.wait_for(authenticated.wait(), 5)
    change_task = asyncio.create_task(
        accounts.client.patch(
            "/api/auth/me",
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
            },
        )
    )
    try:
        # An already authenticated sign-in holds the user row until its session exists.
        # A password change must either wait for it or revoke that completed session.
        try:
            await asyncio.wait_for(asyncio.shield(change_task), 0.5)
        except TimeoutError:
            pass
    finally:
        release.set()
    login_response, change_response = await asyncio.gather(login_task, change_task)
    assert login_response.status_code == change_response.status_code == 200
    async with SessionLocal() as db:
        concurrent = await db.scalar(
            select(UserSession).where(UserSession.token_hash == hash_token(token))
        )
        current = await db.scalar(
            select(UserSession).where(UserSession.token_hash == hash_token(accounts.tokens[0]))
        )
        assert concurrent.revoked_at is not None
        assert current.revoked_at is None
