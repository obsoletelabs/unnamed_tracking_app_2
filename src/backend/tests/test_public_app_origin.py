"""Public plugin pages never execute with the host application's cookie authority."""

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request, Response

from src.api.routes import public_apps as public_routes
from src.core.config import settings
from src.core.public_app_origin import PublicAppOriginMiddleware

pytest_plugins = ["tests.test_public_apps"]


@pytest.fixture
def isolated_app(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_APPS_ORIGIN", "https://apps.example")
    app = FastAPI()

    @app.get("/api/auth/me")
    async def private():
        return {"private": True}

    @app.get("/api/public-apps/example/config")
    async def public(request: Request, response: Response):
        response.set_cookie("must-not-be-set", "host-secret")
        return {
            "cookie": request.headers.get("cookie"),
            "authorization": request.headers.get("authorization"),
        }

    app.add_middleware(PublicAppOriginMiddleware)
    return app


async def test_public_hostname_has_only_public_routes_and_no_cookie_authority(isolated_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=isolated_app), base_url="https://apps.example"
    ) as client:
        private = await client.get("/api/auth/me", headers={"Cookie": "session=admin"})
        assert private.status_code == 403
        public = await client.get(
            "/api/public-apps/example/config",
            headers={
                "Cookie": "session=admin; oidc_state=secret",
                "Authorization": "Bearer participant",
            },
        )
        assert public.status_code == 200
        assert public.json() == {"cookie": None, "authorization": "Bearer participant"}
        assert "set-cookie" not in public.headers


async def test_public_script_cannot_call_main_host_api_even_with_cookies(isolated_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=isolated_app), base_url="https://main.example"
    ) as client:
        response = await client.get(
            "/api/auth/me",
            headers={"Origin": "https://apps.example", "Cookie": "session=admin"},
        )
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
        assert (await client.get("/api/auth/me")).status_code == 200


@pytest.mark.parametrize(
    ("configured", "main", "request_origin", "status"),
    [
        ("", "https://main.example", "https://main.example", 503),
        ("https://apps.example", "https://main.example", "https://main.example", 404),
        ("https://apps.example:8443", "https://apps.example", "https://apps.example:8443", 503),
        ("https://apps.example", "https://main.example", "https://apps.example", 200),
    ],
)
async def test_public_assets_require_a_separate_configured_hostname(
    public_flow, monkeypatch, configured, main, request_origin, status
):
    monkeypatch.setattr(settings, "PUBLIC_APPS_ORIGIN", configured)
    monkeypatch.setattr(public_routes, "get_public_app_url", AsyncMock(return_value=main))
    asset = AsyncMock(return_value=b"<html>public app</html>")
    monkeypatch.setattr(public_routes.apps.client, "frontend_asset", asset)
    response = await public_flow.client.get(request_origin + public_flow.base + "/app/")
    assert response.status_code == status, response.text
    if status == 200:
        asset.assert_awaited_once()
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "connect-src 'self'" in response.headers["content-security-policy"]
    else:
        asset.assert_not_awaited()
