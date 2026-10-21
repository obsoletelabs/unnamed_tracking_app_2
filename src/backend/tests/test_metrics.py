"""The Prometheus endpoint is closed by default, authenticated, bounded and free of private data."""

import re

import httpx
import pytest

from src.core import metrics
from src.core.config import Settings, settings
from src.main import app

TOKEN = "t" * 40


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "METRICS_TOKEN", TOKEN)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as http:
        yield http


def bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_disabled_by_default_is_not_found(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_ENABLED", False)
    for headers in ({}, bearer()):
        response = await client.get("/metrics", headers=headers)
        assert response.status_code == 404
        assert "uta_" not in response.text


async def test_requires_the_scrape_token(client, enabled):
    missing = await client.get("/metrics")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    for headers in (
        bearer("wrong" * 10),
        bearer(TOKEN[:-1]),
        bearer(TOKEN + "x"),
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
    ):
        assert (await client.get("/metrics", headers=headers)).status_code == 401


async def test_serves_exposition_with_a_valid_token(client, enabled):
    response = await client.get("/metrics", headers=bearer())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"
    assert "uta_http_requests_total" in response.text or "uta_build_info" in response.text
    assert "uta_database_up 1.0" in response.text
    assert "process_cpu_seconds_total" in response.text
    assert (await client.head("/metrics", headers=bearer())).status_code == 200
    assert (await client.post("/metrics", headers=bearer())).status_code == 405


async def test_is_not_an_api_route_or_reachable_under_the_api_prefix(client, enabled):
    assert all(getattr(route, "path", "") != "/metrics" for route in app.routes)
    assert "/metrics" not in (await client.get("/api/openapi.json")).json()["paths"]
    response = await client.get("/api/metrics", headers=bearer())
    assert response.status_code != 200
    assert "uta_" not in response.text


async def test_labels_use_route_templates_not_raw_urls(client, enabled):
    hostile = "/api/games/00000000-0000-0000-0000-00000000dead?q=secret-title&token=abc123"
    await client.get(hostile)
    await client.get("/no/such/place/for-user-alice@example.test")
    body = (await client.get("/metrics", headers=bearer())).text
    assert "dead" not in body
    assert "secret-title" not in body
    assert "alice" not in body
    assert "abc123" not in body
    assert 'route="unmatched"' in body or 'route="/' in body
    routes = set(re.findall(r'uta_http_requests_total\{[^}]*route="([^"]*)"', body))
    assert all(
        "{" in route or route in {"unmatched", "/health"} or "/" in route for route in routes
    )


async def test_cardinality_is_bounded_by_the_route_table(client, enabled):
    before = len(
        set(re.findall(r'route="([^"]*)"', (await client.get("/metrics", headers=bearer())).text))
    )
    for number in range(200):
        await client.get(f"/api/does-not-exist/{number}")
        await client.get(f"/api/games/{number}-x")
    body = (await client.get("/metrics", headers=bearer())).text
    after = len(set(re.findall(r'route="([^"]*)"', body)))
    assert after - before <= 3
    assert "does-not-exist" not in body


async def test_exceptions_and_rate_limits_are_counted(client, enabled):
    def total(name: str, route: str) -> float:
        value = metrics.REGISTRY.get_sample_value(name, {"route": route})
        return value or 0.0

    @app.get("/__metrics_boom/{item}", include_in_schema=False)
    async def boom(item: str):
        raise RuntimeError(item)

    # The plugin catch-all is registered last; a test route must precede it.
    app.router.routes.insert(0, app.router.routes.pop())
    try:
        before = total("uta_http_exceptions_total", "/__metrics_boom/{item}")
        response = await client.get("/__metrics_boom/private-value")
        assert response.status_code == 500
        assert total("uta_http_exceptions_total", "/__metrics_boom/{item}") == before + 1
    finally:
        app.router.routes[:] = [
            route for route in app.routes if getattr(route, "path", "") != "/__metrics_boom/{item}"
        ]
    assert "private-value" not in (await client.get("/metrics", headers=bearer())).text


async def test_runtime_failure_is_reported_without_failing_the_scrape(client, enabled, monkeypatch):
    monkeypatch.setenv("PLUGIN_RUNTIME_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("PLUGIN_RUNTIME_TOKEN", "r" * 40)
    for snapshot in metrics._snapshots:  # pylint: disable=protected-access
        snapshot.refreshed_at = float("-inf")
    response = await client.get("/metrics", headers=bearer())
    assert response.status_code == 200
    assert "uta_plugin_runtime_up 0.0" in response.text
    assert 'uta_plugin_runtime_requests_total{operation="GET /health",outcome="unreachable"}' in (
        response.text
    )
    assert "r" * 40 not in response.text


async def test_concurrent_scrapes_and_requests(client, enabled):
    import asyncio

    results = await asyncio.gather(
        *(client.get("/metrics", headers=bearer()) for _ in range(10)),
        *(client.get("/health") for _ in range(10)),
    )
    assert all(result.status_code == 200 for result in results)
    assert (await client.get("/metrics", headers=bearer())).text.count(
        "uta_http_requests_in_flight 1.0"
    ) <= 1


def test_enabling_without_a_strong_token_is_a_configuration_error(monkeypatch):
    base = {"DATABASE_URL": "postgresql+psycopg://u:p@h/d", "METRICS_ENABLED": True}
    for token in ("", "short", "x" * 31):
        with pytest.raises(ValueError, match="METRICS_TOKEN"):
            Settings(_env_file=None, METRICS_TOKEN=token, **base)  # type: ignore[call-arg]
    assert Settings(_env_file=None, METRICS_TOKEN="x" * 32, **base).METRICS_ENABLED  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/plugins", "GET /plugins"),
        ("POST", "/plugins/com.example.secret/start", "POST /plugins/{id}/start"),
        ("GET", "/plugins/abc/frontend/deep/file.js", "GET /plugins/{id}/frontend"),
        ("GET", "/plugins/abc", "GET /plugins/{id}"),
        ("GET", "/health", "GET /health"),
        ("GET", "/weird/path", "other"),
    ],
)
def test_runtime_operations_never_contain_plugin_ids(method, path, expected):
    assert metrics.plugin_runtime_operation(method, path) == expected
