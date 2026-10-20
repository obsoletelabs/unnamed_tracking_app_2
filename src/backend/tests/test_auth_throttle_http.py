"""Real HTTP routes, PostgreSQL counters and credentials, without external providers."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select, update

from src.api.routes import auth_oidc
from src.core import password_work
from src.core.auth import hash_password, hash_token, session_cookie_name
from src.core.config import settings
from src.database.models.auth import UserApiKey, UserSession
from src.database.models.plugin_auth_flow import AuthRequestLimit
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import auth_throttle
from src.main import app


@pytest.fixture
async def account(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LOGIN_ACCOUNT_PER_MINUTE", 2)
    monkeypatch.setattr(settings, "AUTH_LOGIN_ACCOUNT_PER_15_MINUTES", 4)
    monkeypatch.setattr(settings, "AUTH_LOGIN_IP_FAILURES_PER_15_MINUTES", 10)
    identity = uuid4()
    token = "test-browser-" + identity.hex
    api_key = "utk_test-" + identity.hex
    async with SessionLocal() as db:
        user = User(
            id=identity,
            username="quota-" + identity.hex,
            email=identity.hex + "@example.test",
            password_hash=hash_password("Correct-Password-123!"),
            is_active=True,
            is_admin=False,
        )
        db.add(user)
        await db.flush()
        db.add_all(
            [
                UserSession(
                    user_id=identity,
                    token_hash=hash_token(token),
                    expires_at=int(time.time()) + 3600,
                ),
                UserApiKey(
                    user_id=identity,
                    name="quota-test",
                    key_prefix=api_key[:12],
                    key_hash=hash_token(api_key),
                ),
            ]
        )
        await db.commit()
    yield SimpleNamespace(
        id=identity, username=user.username, email=user.email, token=token, api_key=api_key
    )
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == identity))
        await db.commit()


def client(peer="192.0.2.91", **kwargs):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
        base_url="http://test",
        **kwargs,
    )


def assert_retry(response, status=429):
    assert response.status_code == status, response.text
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["Cache-Control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert response.json() == {
        "detail": (
            "Too many authentication requests. Try again later."
            if status == 429
            else "Authentication protection is temporarily unavailable. Try again shortly."
        )
    }


@pytest.mark.parametrize("kind", ["known", "unknown", "inactive"])
async def test_failed_login_is_generic_and_recovers_after_expiry(account, kind):
    identifier = account.username if kind != "unknown" else "missing-" + uuid4().hex
    if kind == "inactive":
        async with SessionLocal() as db:
            await db.execute(update(User).where(User.id == account.id).values(is_active=False))
            await db.commit()
    async with client() as http:
        for _ in range(2):
            response = await http.post(
                "/api/auth/login", json={"username_or_email": identifier, "password": "wrong"}
            )
            assert response.status_code == 401 and response.json() == {
                "detail": "Invalid credentials."
            }
        assert_retry(
            await http.post(
                "/api/auth/login", json={"username_or_email": identifier, "password": "wrong"}
            )
        )
        async with SessionLocal() as db:
            await db.execute(update(AuthRequestLimit).values(expires_at=0))
            await db.commit()
        assert (
            await http.post(
                "/api/auth/login", json={"username_or_email": identifier, "password": "wrong"}
            )
        ).status_code == 401


async def test_success_refunds_failed_account_budget_and_aliases_share_account(account):
    async with client() as http:
        for _ in range(4):
            response = await http.post(
                "/api/auth/login",
                json={"username_or_email": account.username, "password": "Correct-Password-123!"},
            )
            assert response.status_code == 200 and response.cookies.get(session_cookie_name("test"))
        for identifier in (account.username, account.email):
            assert (
                await http.post(
                    "/api/auth/login", json={"username_or_email": identifier, "password": "wrong"}
                )
            ).status_code == 401
        assert_retry(
            await http.post(
                "/api/auth/login",
                json={"username_or_email": account.email.upper(), "password": "wrong"},
            )
        )


async def test_failed_login_caller_budget_cannot_be_rotated_with_identifiers_or_headers(
    account, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_LOGIN_IP_FAILURES_PER_15_MINUTES", 2)
    async with client() as http:
        for index in range(3):
            response = await http.post(
                "/api/auth/login",
                json={"username_or_email": uuid4().hex, "password": "wrong"},
                headers={
                    "X-Real-IP": f"198.51.100.{index}",
                    "X-Forwarded-For": f"198.51.100.{index}",
                },
            )
            assert response.status_code == 401 if index < 2 else response.status_code == 429
    async with client("192.0.2.92") as http:
        assert (
            await http.post(
                "/api/auth/login", json={"username_or_email": uuid4().hex, "password": "wrong"}
            )
        ).status_code == 401


async def test_ipv6_privacy_addresses_share_a_caller_budget(account, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LOGIN_IP_FAILURES_PER_15_MINUTES", 1)
    for peer, expected in (
        ("2001:db8:1234:5678::1", 401),
        ("2001:db8:1234:5678:aaaa::2", 429),
        ("2001:db8:1234:5679::1", 401),
    ):
        async with client(peer) as http:
            response = await http.post(
                "/api/auth/login", json={"username_or_email": uuid4().hex, "password": "wrong"}
            )
            assert response.status_code == expected


async def test_invalid_credentials_share_budget_across_supported_namespaces(account, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_PERCENT", 10)  # 30 failed checks/minute.
    attempts = [
        ("/api/auth/me", {"Authorization": "Bearer utk_invalid"}),
        ("/api/auth/me", {"Cookie": f"{session_cookie_name('test')}=invalid"}),
        ("/api/plugins", {"Authorization": "Bearer utpm_invalid"}),
        ("/api/delegation/v1/credential", {"Authorization": "Bearer utd_invalid"}),
    ]
    async with client() as http:
        for index in range(30):
            path, headers = attempts[index % len(attempts)]
            response = await http.get(path, headers=headers)
            assert response.status_code == 401, response.text
        assert_retry(
            await http.get("/api/auth/me", headers={"Authorization": "Bearer utk_invalid"})
        )


async def test_valid_credentials_and_permission_denials_are_not_failed_checks(account, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_PERCENT", 10)
    async with client(headers={"Authorization": "Bearer " + account.api_key}) as http:
        for _ in range(32):
            assert (await http.get("/api/auth/me")).status_code == 200
            assert (await http.get("/api/settings/system-info")).status_code == 403
        assert (await http.get("/api/auth/me")).status_code == 200
    async with SessionLocal() as db:
        assert all(count == 0 for count in await db.scalars(select(AuthRequestLimit.count)))


async def test_api_key_issuance_has_actor_quota_and_preserves_browser_requirement(
    account, monkeypatch
):
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_PERCENT", 10)  # One new key/hour/actor.
    async with client(cookies={session_cookie_name("test"): account.token}) as http:
        response = await http.post("/api/auth/api-keys", json={"name": "first"})
        assert response.status_code == 200, response.text
        assert_retry(await http.post("/api/auth/api-keys", json={"name": "second"}))
    async with client(headers={"Authorization": "Bearer " + account.api_key}) as http:
        assert (
            await http.post("/api/auth/api-keys", json={"name": "forbidden"})
        ).status_code == 403


@pytest.mark.parametrize(
    ("path", "maximum"), [("/api/auth/oidc/login", 6), ("/api/auth/oidc/callback/provider", 12)]
)
async def test_oidc_limits_precede_provider_work_and_use_retry_headers(path, maximum, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_PERCENT", 10)
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(auth_oidc, "_get_config", lookup)
    async with client() as http:
        for _ in range(maximum):
            assert (await http.get(path)).status_code == 303
        assert_retry(await http.get(path))
    assert lookup.await_count == maximum


async def test_recovery_account_quota_is_identical_for_missing_and_ineligible_accounts(account):
    async with client() as http:
        for identifier in (account.username, "unknown-" + uuid4().hex):
            for _ in range(5):
                response = await http.post(
                    "/api/auth/password-reset/request", json={"identifier": identifier}
                )
                assert response.status_code == 200
                assert response.json() == {
                    "message": "If the account has an eligible recovery destination, a reset link will arrive."
                }
            assert_retry(
                await http.post("/api/auth/password-reset/request", json={"identifier": identifier})
            )


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/login",
        "/api/auth/user-invites/accept",
        "/api/auth/password-reset/complete",
        "/api/delegation/v1/requests",
    ],
)
async def test_authentication_bodies_are_bounded_even_for_signed_in_callers(account, path):
    async with client(cookies={session_cookie_name("test"): account.token}) as http:
        response = await http.post(path, content=b"x" * 32769)
        assert response.status_code == 413 and response.headers["Cache-Control"] == "no-store"
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize("length", ["-1", "9" * 100, "\u00b2"])
async def test_invalid_declared_auth_body_lengths_are_rejected(length):
    # Exercise ASGI directly: HTTP libraries correctly reject non-ASCII lengths themselves.
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"content-length", length.encode())],
            "client": ("192.0.2.91", 1),
            "server": ("test", 80),
            "scheme": "http",
            "http_version": "1.1",
        },
        receive,
        send,
    )
    assert messages[0]["status"] == 413


async def test_chunked_auth_body_is_rejected_before_credential_or_password_work():
    async def body():
        yield b'{"password":"private"}'

    async with client() as http:
        response = await http.post("/api/auth/login", content=body())
    assert response.status_code == 413 and "private" not in response.text


async def test_store_outage_fails_closed_without_secrets_or_session(account, monkeypatch):
    def unavailable():
        raise auth_throttle.AuthStoreUnavailable

    monkeypatch.setattr(auth_throttle, "AuthLimitSession", unavailable)
    async with client() as http:
        assert_retry(
            await http.post(
                "/api/auth/login",
                json={"username_or_email": account.username, "password": "Correct-Password-123!"},
            ),
            503,
        )
        assert_retry(
            await http.get("/api/auth/me", headers={"Authorization": "Bearer " + account.api_key}),
            503,
        )


async def test_password_capacity_has_bounded_retry_and_does_not_consume_failure_quota(
    account, monkeypatch
):
    class Full:
        def acquire(self, **_kwargs):
            return False

    monkeypatch.setattr(password_work, "_slots", Full())
    async with client() as http:
        response = await http.post(
            "/api/auth/login",
            json={"username_or_email": account.username, "password": "Correct-Password-123!"},
        )
        assert response.status_code == 503 and response.headers["Retry-After"] == "1"
        assert "set-cookie" not in response.headers
    async with SessionLocal() as db:
        # The unconditional request-caller bucket is retained; failure reservations are refunded.
        assert sorted(await db.scalars(select(AuthRequestLimit.count))) == [0, 0, 0, 0, 0, 1]
