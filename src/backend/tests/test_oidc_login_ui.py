"""Public sign-in presentation preserves provider branding and launch controls."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from starlette.middleware.sessions import SessionMiddleware
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
    launch = AsyncMock(return_value=RedirectResponse("https://issuer.invalid/authorize", 302))
    monkeypatch.setattr(auth_oidc, "begin_oidc", launch)
    app = FastAPI()
    app.include_router(auth_oidc.router)
    app.dependency_overrides[get_db] = lambda: db
    return app, row, launch


async def test_multiple_saved_providers_launch_with_authentik_discovery_and_updated_settings(
    boundary, monkeypatch
):
    app, row, _ = boundary
    app.add_middleware(SessionMiddleware, secret_key="isolated-multi-provider-test-only")
    monkeypatch.setattr(auth_oidc, "begin_oidc", oidc.begin_oidc)
    monkeypatch.setattr(oidc, "oauth", oidc.OAuth())
    monkeypatch.setattr(oidc, "_registered_configs", {})
    original_register = oidc.register_oidc_provider

    def register(config, client_name):
        original_register(config, client_name)
        issuer = config.issuer_url.removesuffix("/.well-known/openid-configuration") + "/"

        def metadata(request):
            assert request.url.path.endswith("/.well-known/openid-configuration")
            return Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": issuer + "authorize/",
                    "token_endpoint": issuer + "token/",
                    "jwks_uri": issuer + "jwks/",
                    "userinfo_endpoint": issuer + "userinfo/",
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "token_endpoint_auth_methods_supported": [
                        "client_secret_post",
                        "client_secret_basic",
                    ],
                    "code_challenge_methods_supported": ["plain", "S256"],
                    "response_types_supported": ["code"],
                },
            )

        oidc.oauth.create_client(client_name).client_kwargs["transport"] = MockTransport(metadata)

    monkeypatch.setattr(oidc, "register_oidc_provider", register)
    providers = [
        {
            "name": slug,
            "slug": slug,
            "issuer_url": f"https://issuer.invalid/application/o/{slug}/.well-known/openid-configuration",
            "client_id": f"{slug}-client",
            "client_secret": "encrypted",
        }
        for slug in ("atlas", "authentik")
    ]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://tracking.example.test"
    ) as client:
        for changed in (False, True):
            if changed:
                providers[1]["client_id"] = "authentik-updated-client"
            row.providers_json = json.dumps(providers)
            for provider in providers:
                slug = provider["slug"]
                response = await client.get(f"/api/auth/oidc/login/{slug}?autostart=false")
                assert response.status_code == 302
                parameters = parse_qs(urlsplit(response.headers["location"]).query)
                assert parameters["client_id"] == [provider["client_id"]]
                assert parameters["redirect_uri"] == [
                    f"https://tracking.example.test/api/auth/oidc/callback/{slug}"
                ]
                assert parameters["code_challenge_method"] == ["S256"]
                assert parameters["nonce"]
                assert parameters["state"]


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


@pytest.mark.parametrize(
    ("origin", "headers", "expected_origin"),
    [
        ("https://archive.example.test", {}, "https://archive.example.test"),
        ("https://archive.example.test:9443", {}, "https://archive.example.test:9443"),
        (
            "https://archive.example.test",
            {"host": "alias.example.test:9443"},
            "https://alias.example.test:9443",
        ),
        (
            "https://archive.example.test",
            {"x-forwarded-host": "untrusted.example.test"},
            "https://archive.example.test",
        ),
        (
            "https://archive.example.test",
            {"forwarded": "host=untrusted.example.test"},
            "https://archive.example.test",
        ),
    ],
)
async def test_callback_is_generated_from_the_current_request(
    boundary, monkeypatch, origin, headers, expected_origin
):
    app, row, launch = boundary
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "STARTUP_MODE", "production")
    providers = json.loads(row.providers_json)
    providers[0]["redirect_uri"] = "https://old-address.example.test/api/auth/oidc/callback/branded"
    row.providers_json = json.dumps(providers)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=origin) as client:
        response = await client.get(
            "/api/auth/oidc/login/branded?autostart=false",
            headers=headers,
        )
    assert response.status_code == 302
    request, config = launch.await_args.args
    expected = expected_origin + "/api/auth/oidc/callback/branded"
    assert config.redirect_uri == expected
    assert oidc.callback_url(request, config) == expected
    assert oidc.callback_url(request, replace(config, redirect_uri=None)) == expected


async def test_generated_callback_preserves_the_proxy_mount_path(boundary):
    app, _, launch = boundary
    app.root_path = "/tracking"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://archive.example.test"
    ) as client:
        assert (await client.get("/api/auth/oidc/login/branded?autostart=false")).status_code == 302
    assert launch.await_args.args[1].redirect_uri == (
        "https://archive.example.test/tracking/api/auth/oidc/callback/branded"
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


async def test_named_setup_provider_uses_current_address_after_an_origin_change(boundary):
    app, row, launch = boundary
    row.issuer_url, row.client_id = "https://issuer.invalid", "test-client"
    row.redirect_uri = "https://old-address.example.test/api/auth/oidc/callback"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://current.example.test"
    ) as client:
        assert (await client.get("/api/auth/oidc/login/branded?autostart=false")).status_code == 302
    assert oidc.callback_url(None, launch.await_args.args[1]) == (
        "https://current.example.test/api/auth/oidc/callback/branded"
    )


async def test_environment_callback_applies_to_matching_named_alias(boundary, monkeypatch):
    app, _, launch = boundary
    for name, value in {
        "OIDC_ISSUER_URL": "https://issuer.invalid",
        "OIDC_CLIENT_ID": "test-client",
        "OIDC_CLIENT_SECRET": "secret",
        "OIDC_REDIRECT_URI": "https://canonical.example.test/api/auth/oidc/callback",
    }.items():
        monkeypatch.setattr(settings, name, value)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://attacker.example.test"
    ) as client:
        assert (await client.get("/api/auth/oidc/login/branded?autostart=false")).status_code == 302
    assert oidc.callback_url(None, launch.await_args.args[1]) == (
        "https://canonical.example.test/api/auth/oidc/callback/branded"
    )
