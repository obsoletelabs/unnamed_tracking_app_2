import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes.real_ip import router
from src.core.env_handler import EnvConfigHandler
from src.core.real_ip import (
    DEFAULT_REAL_IP_HEADER,
    DEFAULT_TRUSTED_PROXIES,
    get_effective_real_ip_config,
    get_real_ip_presets,
    validate_real_ip_header,
    validate_trusted_proxies,
)


def test_default_real_ip_config_is_loopback_only():
    config = get_effective_real_ip_config(EnvConfigHandler({}), None, None)
    assert config["header"] == DEFAULT_REAL_IP_HEADER
    assert config["trusted_proxies"] == " ".join(DEFAULT_TRUSTED_PROXIES)


def test_persisted_real_ip_config():
    config = get_effective_real_ip_config(
        EnvConfigHandler({}),
        "CF-Connecting-IP",
        "127.0.0.1/32 100.64.0.0/10",
    )
    assert config["header"] == "CF-Connecting-IP"
    assert config["trusted_proxies"] == "127.0.0.1/32 100.64.0.0/10"


def test_environment_overrides_persisted():
    handler = EnvConfigHandler(
        {
            "NGINX_REALIP_HEADER": "X-Forwarded-For",
            "NGINX_REALIP_TRUSTED_PROXIES": "10.0.0.0/8",
        }
    )
    config = get_effective_real_ip_config(handler, "CF-Connecting-IP", "127.0.0.1/32")
    assert config["trusted_proxies"] == "10.0.0.0/8"


def test_presets_are_backend_owned():
    presets = get_real_ip_presets()
    assert {"local", "cgnat", "cloudflare"} == set(presets)
    assert "100.64.0.0/10" in presets["cgnat"]["values"]


def test_invalid_values():
    try:
        validate_real_ip_header("bad;include /tmp/evil;")
        raise AssertionError("invalid header unexpectedly accepted")
    except ValueError:
        pass
    try:
        validate_trusted_proxies("10.0.0.0/8;include /tmp/evil;")
        raise AssertionError("invalid proxy list unexpectedly accepted")
    except ValueError:
        pass


@pytest.mark.parametrize(
    "value,expected",
    [("192.168.1.42/24\n::1/128 192.168.1.0/24", "192.168.1.0/24 ::1/128"), ("", "")],
)
async def test_proxy_validation_canonicalizes_and_deduplicates_without_configuration_access(
    value, expected
):
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/internal/real-ip/validate", json={"value": value})
        assert response.status_code == 200 and response.json() == {"value": expected}


@pytest.mark.parametrize(
    "value", ["999.1.1.1", "10.0.0.1/34", "2001::broken", "10.0.0.1;include /tmp/evil", "x" * 8193]
)
async def test_proxy_validation_rejects_invalid_entries(value):
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.post("/api/internal/real-ip/validate", json={"value": value})
        ).status_code == 422
