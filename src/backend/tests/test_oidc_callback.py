"""OIDC callback security, account linking, and real browser-session persistence."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from src.api.auth_limits import AuthThrottleMiddleware
from src.api.routes import auth_oidc
from src.core.auth import hash_token, session_cookie_name
from src.core.oidc import OidcConfig
from src.database.models.auth import UserSession
from src.database.models.user import User
from src.database.session import get_db


class CallbackDb:
    """Use a real SQLite session for account and browser-session rows."""

    def __init__(self, session):
        self.session = session

    async def scalar(self, statement):
        return self.session.scalar(statement)

    def add(self, row):
        self.session.add(row)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()


@pytest.fixture
async def callback(monkeypatch):
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    UserSession.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        claims = {
            "email_verified": True,
            "iss": "https://issuer.invalid",
            "sub": "subject",
            "email": "reader@example.test",
            "preferred_username": "reader",
            "nonce": "nonce",
        }
        config = OidcConfig("https://issuer.invalid", "client", "secret", slug="provider")
        client = SimpleNamespace(
            load_server_metadata=AsyncMock(return_value={"issuer": config.issuer_url}),
            framework=SimpleNamespace(
                get_state_data=AsyncMock(
                    return_value={"nonce": "nonce", "code_verifier": "verifier"}
                ),
                clear_state_data=AsyncMock(),
            ),
            _format_state_params=Mock(side_effect=lambda _state, params: params),
            fetch_access_token=AsyncMock(return_value={"id_token": "id-token"}),
            parse_id_token=AsyncMock(return_value=claims),
            userinfo=AsyncMock(return_value=claims),
        )
        selected_config = AsyncMock(return_value=config)
        monkeypatch.setattr(auth_oidc, "_get_config", selected_config)
        monkeypatch.setattr(auth_oidc, "register_oidc_provider", Mock())
        monkeypatch.setattr(auth_oidc.oauth, "create_client", Mock(return_value=client))
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="callback-state-test")
        app.add_middleware(AuthThrottleMiddleware)
        app.include_router(auth_oidc.router)
        app.dependency_overrides[get_db] = lambda: CallbackDb(session)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield SimpleNamespace(
                http=http,
                session=session,
                client=client,
                claims=claims,
                config=config,
                selected_config=selected_config,
            )
    engine.dispose()


def auth_oidc_identity(config, subject="subject"):
    identity = json.dumps([config.expected_issuer, subject], separators=(",", ":"))
    return "oidc-v2:" + hashlib.sha256(identity.encode()).hexdigest()


async def complete(callback, query="?code=code&state=state"):
    return await callback.http.get("/api/auth/oidc/callback" + query)


async def test_oidc_provisions_and_persists_a_cookie_bound_session(callback):
    response = await complete(callback)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?oidc=success"
    user = callback.session.scalar(select(User))
    assert (user.username, user.email, user.oidc_subject) == (
        "reader",
        "reader@example.test",
        auth_oidc_identity(callback.config),
    )
    assert user.is_active and not user.is_admin
    cookie = response.cookies.get(session_cookie_name("test"))
    stored = callback.session.scalar(select(UserSession))
    assert stored.user_id == user.id
    assert stored.token_hash == hash_token(cookie)
    assert cookie not in stored.token_hash
    assert "httponly" in response.headers["set-cookie"].lower()
    callback.client.framework.clear_state_data.assert_awaited_once()
    callback.client._format_state_params.assert_called_once_with(
        {"nonce": "nonce", "code_verifier": "verifier"}, {"code": "code", "state": "state"}
    )


@pytest.mark.parametrize("case", ["missing_id_token", "missing_nonce", "invalid_jwks"])
async def test_oidc_cannot_downgrade_id_token_validation(callback, case):
    if case == "missing_id_token":
        callback.client.fetch_access_token.return_value = {"userinfo": callback.claims}
    else:
        callback.client.fetch_access_token.return_value = {"id_token": "id-token"}
    if case == "missing_nonce":
        callback.client.framework.get_state_data.return_value = {"code_verifier": "verifier"}
    elif case == "invalid_jwks":
        callback.client.parse_id_token.side_effect = ValueError("Invalid key set format")

    response = await complete(callback)

    assert response.headers["location"] == "/login?oidc_error=authentication_failed"
    assert callback.session.scalar(select(User.id)) is None
    assert callback.session.scalar(select(UserSession.id)) is None
    assert session_cookie_name("test") not in response.cookies
    callback.client.userinfo.assert_not_awaited()


@pytest.mark.parametrize(
    "case", ["missing_state", "invalid_state", "provider_failure", "missing_identity"]
)
async def test_oidc_rejects_invalid_exchange_without_account_or_session(callback, case):
    query = "?code=code&state=state"
    if case == "missing_state":
        query = "?code=code"
    elif case == "invalid_state":
        callback.client.framework.get_state_data.return_value = None
    elif case == "provider_failure":
        callback.client.fetch_access_token.side_effect = RuntimeError("Unavailable")
    else:
        callback.claims.pop("sub")
    response = await complete(callback, query)
    reason = "identity_missing" if case == "missing_identity" else "authentication_failed"
    assert response.headers["location"] == f"/login?oidc_error={reason}"
    assert callback.session.scalar(select(User.id)) is None
    assert callback.session.scalar(select(UserSession.id)) is None
    assert session_cookie_name("test") not in response.cookies


@pytest.mark.parametrize(
    ("subject", "active", "error"),
    [(None, False, "account_disabled"), ("other:subject", True, "identity_conflict")],
)
async def test_oidc_cannot_link_disabled_or_conflicting_accounts(callback, subject, active, error):
    user = User(
        username="reader",
        email="reader@example.test",
        password_hash="unused",
        oidc_subject=subject,
        is_active=active,
    )
    callback.session.add(user)
    callback.session.commit()
    response = await complete(callback)
    assert response.headers["location"] == f"/login?oidc_error={error}"
    assert user.oidc_subject == subject
    assert callback.session.scalar(select(UserSession.id)) is None


async def test_oidc_user_creation_policy_is_enforced(callback):
    callback.selected_config.return_value = replace(callback.config, allow_new_users=False)
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=user_creation_disabled"
    assert callback.session.scalar(select(User.id)) is None
    assert callback.session.scalar(select(UserSession.id)) is None


@pytest.mark.parametrize(("groups", "admin"), [(["administrators"], True), (["readers"], False)])
async def test_oidc_links_legacy_subject_and_applies_explicit_admin_group(callback, groups, admin):
    user = User(
        username="reader",
        email="reader@example.test",
        password_hash="unused",
        oidc_subject="subject",
        is_admin=not admin,
    )
    callback.session.add(user)
    callback.session.commit()
    callback.claims["groups"] = groups
    callback.selected_config.return_value = replace(callback.config, admin_group="administrators")
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc=success"
    assert user.oidc_subject == auth_oidc_identity(callback.config)
    assert user.is_admin is admin


@pytest.mark.parametrize("message", ["Invalid key set format", "Invalid nonce"])
async def test_oidc_rejects_invalid_keys_and_nonce(callback, message):
    callback.client.parse_id_token.side_effect = ValueError(message)
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=authentication_failed"
    callback.client.userinfo.assert_not_awaited()
    assert callback.session.scalar(select(UserSession.id)) is None


async def test_oidc_provider_errors_do_not_log_response_secrets(callback, caplog):
    callback.client.fetch_access_token.side_effect = RuntimeError(
        "access_token=private-token client_secret=private-secret"
    )
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=authentication_failed"
    assert callback.session.scalar(select(UserSession.id)) is None
    assert "RuntimeError" in caplog.text
    assert "private-token" not in caplog.text and "private-secret" not in caplog.text


@pytest.mark.parametrize("verified", [False, None, "true", 1, "missing"])
@pytest.mark.parametrize("account", ["new", "email_match", "subject_bound"])
async def test_required_verified_email_rejects_before_any_account_mutation(
    callback, verified, account
):
    user = None
    if account != "new":
        user = User(
            username="reader",
            email="reader@example.test" if account == "email_match" else "old@example.test",
            password_hash="unused",
            oidc_subject="provider:subject" if account == "subject_bound" else None,
            is_admin=False,
        )
        callback.session.add(user)
        callback.session.commit()
    if verified != "missing":
        callback.claims["email_verified"] = verified
    else:
        callback.claims.pop("email_verified", None)
    callback.claims["groups"] = ["administrators"]
    callback.selected_config.return_value = replace(
        callback.config, require_verified_email=True, admin_group="administrators"
    )
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=verified_email_required"
    assert session_cookie_name("test") not in response.cookies
    assert callback.session.scalar(select(UserSession.id)) is None
    if user is None:
        assert callback.session.scalar(select(User.id)) is None
    else:
        callback.session.refresh(user)
        assert not user.is_admin
        assert user.email == (
            "reader@example.test" if account == "email_match" else "old@example.test"
        )
        assert user.oidc_subject == ("provider:subject" if account == "subject_bound" else None)


@pytest.mark.parametrize("verified", [False, None, "true", 1, "missing"])
async def test_optional_verified_email_preserves_existing_provider_compatibility(
    callback, verified
):
    if verified != "missing":
        callback.claims["email_verified"] = verified
    else:
        callback.claims.pop("email_verified", None)
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc=success"
    assert callback.session.scalar(select(UserSession.id)) is not None


async def test_required_verified_email_accepts_explicit_boolean_true(callback):
    callback.claims["email_verified"] = True
    callback.selected_config.return_value = replace(callback.config, require_verified_email=True)
    assert (await complete(callback)).headers["location"] == "/login?oidc=success"
    assert callback.session.scalar(select(UserSession.id)) is not None


@pytest.mark.parametrize("subject_bound", [True, False])
async def test_oidc_cannot_adopt_another_accounts_email(callback, subject_bound):
    user = User(
        username="reader",
        email="original@example.test",
        password_hash="unused",
        oidc_subject="provider:subject" if subject_bound else None,
        is_admin=False,
    )
    owner = User(username="owner", email="reader@example.test", password_hash="unused")
    callback.session.add_all([user, owner])
    callback.session.commit()
    callback.selected_config.return_value = replace(callback.config, user_match_field="username")
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=email_conflict"
    callback.session.refresh(user)
    assert user.email == "original@example.test"
    assert user.oidc_subject == ("provider:subject" if subject_bound else None)
    assert callback.session.scalar(select(UserSession.id)) is None


async def test_oidc_email_uniqueness_race_rolls_back_without_a_session(callback, monkeypatch):
    original_create = auth_oidc._create_oidc_user

    async def create_after_another_account_claims_email(db, *args, **kwargs):
        # Represent a different transaction committing after the availability lookup.
        callback.session.add(
            User(username="concurrent-owner", email="reader@example.test", password_hash="unused")
        )
        callback.session.commit()
        return await original_create(db, *args, **kwargs)

    monkeypatch.setattr(auth_oidc, "_create_oidc_user", create_after_another_account_claims_email)
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=identity_conflict"
    assert session_cookie_name("test") not in response.cookies
    assert callback.session.scalar(select(UserSession.id)) is None
    users = callback.session.scalars(select(User)).all()
    assert len(users) == 1 and users[0].username == "concurrent-owner"
    assert users[0].oidc_subject is None


async def test_oidc_new_account_gets_display_name_but_ascii_username(callback):
    callback.claims["preferred_username"] = "reader"
    callback.claims["name"] = "  Rénée   Müller 🎮 "
    assert (await complete(callback)).headers["location"] == "/login?oidc=success"
    user = callback.session.scalar(select(User))
    assert (user.username, user.display_name) == ("reader", "Rénée Müller 🎮")


@pytest.mark.parametrize("name", ["bad\nname", "spoof‮name", "x" * 65, ""])
async def test_oidc_unsafe_or_missing_name_claim_leaves_display_name_unset(callback, name):
    callback.claims["name"] = name
    assert (await complete(callback)).headers["location"] == "/login?oidc=success"
    assert callback.session.scalar(select(User)).display_name is None


async def test_oidc_login_never_renames_username_or_overwrites_display_name(callback):
    user = User(
        username="Local User",
        display_name="My Chosen Name",
        email="reader@example.test",
        password_hash="unused",
        oidc_subject="provider:subject",
    )
    callback.session.add(user)
    callback.session.commit()
    callback.claims.update({"preferred_username": "someone-else", "name": "Provider Name"})
    assert (await complete(callback)).headers["location"] == "/login?oidc=success"
    callback.session.refresh(user)
    assert (user.username, user.display_name) == ("Local User", "My Chosen Name")
    assert callback.session.scalar(select(UserSession.user_id)) == user.id


async def test_oidc_name_claim_cannot_select_an_account_by_username(callback):
    victim = User(username="Admin User", email="admin@example.test", password_hash="unused")
    callback.session.add(victim)
    callback.session.commit()
    callback.claims.pop("preferred_username")
    callback.claims["name"] = "Admin User"
    callback.selected_config.return_value = replace(callback.config, user_match_field="username")
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=identity_missing"
    callback.session.refresh(victim)
    assert victim.oidc_subject is None
    assert callback.session.scalar(select(UserSession.id)) is None


async def test_oidc_new_account_with_taken_username_gets_distinct_account(callback):
    existing = User(username="reader", email="other@example.test", password_hash="unused")
    callback.session.add(existing)
    callback.session.commit()
    assert (await complete(callback)).headers["location"] == "/login?oidc=success"
    users = {u.username: u for u in callback.session.scalars(select(User))}
    assert set(users) == {"reader", "reader-2"}
    assert users["reader"].oidc_subject is None


@pytest.mark.parametrize("old_subject", [None, "subject", "provider:subject"])
@pytest.mark.parametrize("match_field", ["email", "username"])
async def test_unverified_email_never_links_existing_account(callback, old_subject, match_field):
    user = User(
        username="reader",
        email="reader@example.test",
        password_hash="unused",
        oidc_subject=old_subject,
    )
    callback.session.add(user)
    callback.session.commit()
    callback.claims["email_verified"] = False
    callback.selected_config.return_value = replace(callback.config, user_match_field=match_field)
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=verified_email_required"
    assert user.oidc_subject == old_subject
    assert callback.session.scalar(select(UserSession.id)) is None


async def test_username_match_also_requires_same_verified_email(callback):
    user = User(
        username="reader", email="victim@example.test", password_hash="unused", is_admin=True
    )
    callback.session.add(user)
    callback.session.commit()
    callback.selected_config.return_value = replace(callback.config, user_match_field="username")
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=identity_conflict"
    assert user.email == "victim@example.test" and user.oidc_subject is None and user.is_admin
    assert callback.session.scalar(select(UserSession.id)) is None


@pytest.mark.parametrize("subject", [" subject", "subject ", "SUBJECT"])
async def test_opaque_subject_is_not_trimmed_or_case_folded(callback, subject):
    user = User(
        username="reader",
        email="reader@example.test",
        password_hash="unused",
        oidc_subject=auth_oidc_identity(callback.config),
    )
    callback.session.add(user)
    callback.session.commit()
    callback.claims["sub"] = subject
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=identity_conflict"
    assert user.oidc_subject == auth_oidc_identity(callback.config)
    assert callback.session.scalar(select(UserSession.id)) is None


async def test_reusing_provider_slug_cannot_rebind_a_different_issuer(callback):
    user = User(
        username="reader",
        email="reader@example.test",
        password_hash="unused",
        oidc_subject=auth_oidc_identity(callback.config),
    )
    callback.session.add(user)
    callback.session.commit()
    callback.selected_config.return_value = replace(
        callback.config, issuer_url="https://other.example.test"
    )
    callback.client.load_server_metadata.return_value = {"issuer": "https://other.example.test"}
    callback.claims["iss"] = "https://other.example.test"
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc_error=identity_conflict"
    assert user.oidc_subject == auth_oidc_identity(callback.config)
    assert callback.session.scalar(select(UserSession.id)) is None


async def test_renaming_provider_slug_preserves_issuer_subject_identity(callback):
    user = User(
        username="reader",
        email="reader@example.test",
        password_hash="unused",
        oidc_subject=auth_oidc_identity(callback.config),
    )
    callback.session.add(user)
    callback.session.commit()
    callback.selected_config.return_value = replace(
        callback.config, slug="renamed", user_match_field="username"
    )
    callback.claims.pop("preferred_username")
    response = await complete(callback)
    assert response.headers["location"] == "/login?oidc=success"
    assert callback.session.scalar(select(UserSession.user_id)) == user.id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sub", 1),
        ("sub", True),
        ("sub", ["subject"]),
        ("sub", {"sub": "subject"}),
        ("sub", "x" * 256),
        ("email", 1),
        ("email", {"email": "reader@example.test"}),
    ],
)
async def test_identity_claims_require_strings(callback, field, value):
    callback.claims[field] = value
    response = await complete(callback)
    reason = "authentication_failed" if field == "email" else "identity_missing"
    assert response.headers["location"] == f"/login?oidc_error={reason}"
    assert callback.session.scalar(select(User.id)) is None
    assert callback.session.scalar(select(UserSession.id)) is None


async def test_verified_email_linking_uses_case_insensitive_ownership(callback):
    user = User(username="reader", email="Reader@Example.Test", password_hash="unused")
    callback.session.add(user)
    callback.session.commit()
    assert (await complete(callback)).headers["location"] == "/login?oidc=success"
    assert user.oidc_subject == auth_oidc_identity(callback.config)
    assert callback.session.scalar(select(UserSession.user_id)) == user.id


async def test_ambiguous_legacy_email_case_never_selects_an_account(callback):
    users = [
        User(username="reader", email="Reader@Example.Test", password_hash="unused"),
        User(username="other", email="reader@example.test", password_hash="unused"),
    ]
    callback.session.add_all(users)
    callback.session.commit()
    assert (await complete(callback)).headers["location"] == "/login?oidc_error=email_conflict"
    assert all(user.oidc_subject is None for user in users)
    assert callback.session.scalar(select(UserSession.id)) is None


async def test_group_claim_values_are_not_coerced_into_admin_group_names(callback):
    callback.claims["groups"] = [1, {"group": "administrators"}]
    callback.selected_config.return_value = replace(callback.config, admin_group="1")
    assert (await complete(callback)).headers["location"] == "/login?oidc=success"
    assert not callback.session.scalar(select(User)).is_admin
