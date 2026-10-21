"""Public sign-in presentation preserves provider branding and launch controls."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.responses import RedirectResponse

from src.api.routes import auth_oidc
from src.core import oidc
from src.core.config import settings
from src.database.session import get_db


def test_saved_provider_changes_replace_cached_oidc_client(monkeypatch):
    monkeypatch.setattr(oidc, "oauth", oidc.OAuth())
    monkeypatch.setattr(oidc, "_registered_configs", {})
    first = oidc.OidcConfig("https://first.invalid", "first-client", "first-secret")
    oidc.register_oidc_provider(first, "review")
    first_client = oidc.oauth.create_client("review")
    oidc.register_oidc_provider(first, "review")
    assert oidc.oauth.create_client("review") is first_client
    second = oidc.OidcConfig("https://second.invalid", "second-client", "second-secret")
    oidc.register_oidc_provider(second, "review")
    second_client = oidc.oauth.create_client("review")
    assert second_client is not first_client
    assert second_client.client_id == "second-client"
    assert second_client.client_secret == "second-secret"
    assert (
        second_client._server_metadata_url
        == "https://second.invalid/.well-known/openid-configuration"
    )


@pytest.fixture
def boundary(monkeypatch):
    providers = [
        {
            "name": "Branded provider",
            "slug": "branded",
            "issuer_url": "https://issuer.invalid",
            "client_id": "test-client",
            "client_secret": "encrypted",
            "button_colour": "#7557e8",
            "autostart_enabled": False,
        },
        {"name": "Themed provider", "slug": "themed", "button_colour": ""},
        {"name": "Hidden provider", "slug": "hidden", "show_on_login": False},
    ]
    row = SimpleNamespace(
        enabled=True,
        providers_json=json.dumps(providers),
        issuer_url=None,
        require_verified_email=False,
        client_id=None,
        client_secret=None,
        default_login_method="sso",
        login_button_text="Continue with SSO",
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=row))
    for name in ("OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "OIDC_REQUIRE_VERIFIED_EMAIL", False)
    monkeypatch.setattr(auth_oidc, "decrypt_secret", lambda value: "test-secret")
    monkeypatch.setattr(
        auth_oidc, "get_public_app_url", AsyncMock(return_value="https://tracking.example.test")
    )
    launch = AsyncMock(return_value=RedirectResponse("https://issuer.invalid/authorize", 302))
    monkeypatch.setattr(auth_oidc, "begin_oidc", launch)
    app = FastAPI()
    app.include_router(auth_oidc.router)
    app.dependency_overrides[get_db] = lambda: db
    return app, row, launch


@pytest.mark.asyncio
async def test_public_status_exposes_only_sign_in_presentation(boundary):
    app, _, _ = boundary
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/auth/oidc/status")
    assert response.status_code == 200
    providers = response.json()["providers"]
    assert [item["slug"] for item in providers] == ["branded", "themed"]
    assert providers[0]["button_color"] == "#7557e8"
    assert providers[0]["autostart_enabled"] is False
    assert providers[1]["button_color"] is None
    assert all("client_secret" not in item and "client_id" not in item for item in providers)


@pytest.mark.asyncio
async def test_manual_button_can_launch_provider_with_autostart_disabled(boundary):
    app, _, launch = boundary
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.get("/api/auth/oidc/login/branded")
        assert blocked.status_code == 303
        assert blocked.headers["location"] == "/login?oidc_error=not_configured"
        launch.assert_not_awaited()
        manual = await client.get("/api/auth/oidc/login/branded?autostart=false")
    assert manual.status_code == 302
    config = launch.await_args.args[1]
    assert config.slug == "branded"
    assert config.client_id == "test-client"


@pytest.mark.parametrize("source", ["named", "legacy", "environment"])
async def test_requirement_cannot_be_bypassed_through_a_callback_alias(
    boundary, monkeypatch, source
):
    app, row, launch = boundary
    row.issuer_url = "https://issuer.invalid/.well-known/openid-configuration"
    row.client_id = "test-client"
    row.client_secret = "encrypted"
    row.scopes = "openid profile email"
    row.groups_claim = "groups"
    row.admin_group = None
    row.user_match_field = "email"
    row.allow_new_users = True
    providers = json.loads(row.providers_json)
    providers[0]["require_verified_email"] = source == "named"
    row.providers_json = json.dumps(providers)
    row.require_verified_email = source == "legacy"
    if source == "environment":
        for name, value in {
            "OIDC_ISSUER_URL": "https://issuer.invalid",
            "OIDC_CLIENT_ID": "test-client",
            "OIDC_CLIENT_SECRET": "test-secret",
            "OIDC_REQUIRE_VERIFIED_EMAIL": True,
        }.items():
            monkeypatch.setattr(settings, name, value)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/api/auth/oidc/login", "/api/auth/oidc/login/branded?autostart=false"):
            assert (await client.get(path)).status_code == 302
            assert launch.await_args.args[1].require_verified_email is True


async def test_environment_requirement_does_not_restrict_a_different_client(boundary, monkeypatch):
    app, _, launch = boundary
    for name, value in {
        "OIDC_ISSUER_URL": "https://issuer.invalid",
        "OIDC_CLIENT_ID": "different-client",
        "OIDC_CLIENT_SECRET": "secret",
        "OIDC_REQUIRE_VERIFIED_EMAIL": True,
    }.items():
        monkeypatch.setattr(settings, name, value)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/auth/oidc/login/branded?autostart=false")
    assert launch.await_args.args[1].require_verified_email is False


@pytest.mark.asyncio
async def test_unconfigured_entries_return_to_the_themed_login(boundary):
    app, row, launch = boundary
    row.enabled = False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/login", "/login/missing", "/callback", "/callback/missing"):
            response = await client.get("/api/auth/oidc" + path)
            assert response.status_code == 303
            assert response.headers["location"] == "/login?oidc_error=not_configured"
    launch.assert_not_awaited()


@pytest.mark.parametrize("header_name", ["host", "x-forwarded-host", "forwarded"])
async def test_request_headers_cannot_replace_canonical_callback(boundary, header_name):
    app, _, launch = boundary
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://attacker.example.test"
    ) as client:
        response = await client.get(
            "/api/auth/oidc/login/branded?autostart=false",
            headers={header_name: "attacker.example.test"},
        )
    assert response.status_code == 302
    assert launch.await_args.args[1].redirect_uri == (
        "https://tracking.example.test/api/auth/oidc/callback/branded"
    )


async def test_explicit_environment_redirect_is_preserved(boundary, monkeypatch):
    app, _, launch = boundary
    for name, value in {
        "OIDC_ISSUER_URL": "https://issuer.invalid",
        "OIDC_CLIENT_ID": "client",
        "OIDC_CLIENT_SECRET": "secret",
        "OIDC_REDIRECT_URI": "https://canonical.example.test/api/auth/oidc/callback",
    }.items():
        monkeypatch.setattr(settings, name, value)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://attacker.example.test"
    ) as client:
        assert (await client.get("/api/auth/oidc/login")).status_code == 302
    assert launch.await_args.args[1].redirect_uri == (
        "https://canonical.example.test/api/auth/oidc/callback"
    )
