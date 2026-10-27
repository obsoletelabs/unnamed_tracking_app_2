"""The direct metrics listener exposes only the live, unauthenticated scrape route."""

import httpx
import pytest

from src.api.metrics_listener import MetricsListener
from src.core.config import settings
from src.main import app


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "METRICS_ENABLED", True)
    monkeypatch.setattr(settings, "METRICS_TOKEN", "t" * 40)


async def test_direct_listener_is_isolated_and_shares_live_registry(enabled, unused_tcp_port):
    listener = MetricsListener("127.0.0.1", unused_tcp_port)
    await listener.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{unused_tcp_port}") as direct:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://api"
            ) as api:
                assert (await api.get("/metrics")).status_code == 401
                assert (await api.get("/health")).status_code == 200
            scrape = await direct.get("/metrics")
            assert scrape.status_code == 200
            assert scrape.headers["content-type"].startswith("text/plain")
            assert scrape.headers["cache-control"] == "no-store"
            assert 'route="/health"' in scrape.text
            assert (await direct.head("/metrics")).status_code == 200
            assert (await direct.head("/metrics")).content == b""
            assert (await direct.post("/metrics")).status_code == 405
            for path in ("/api/games", "/docs", "/health", "/plugin", "/other"):
                assert (await direct.get(path)).status_code == 404
    finally:
        await listener.stop()


async def test_direct_listener_releases_port_and_reports_conflicts(enabled, unused_tcp_port):
    first = MetricsListener("127.0.0.1", unused_tcp_port)
    await first.start()
    second = MetricsListener("127.0.0.1", unused_tcp_port)
    try:
        with pytest.raises(RuntimeError, match="Cannot bind metrics listener"):
            await second.start()
    finally:
        await first.stop()
    await second.start()
    await second.stop()


async def test_direct_listener_respects_disabled_metrics(monkeypatch, unused_tcp_port):
    monkeypatch.setattr(settings, "METRICS_ENABLED", False)
    listener = MetricsListener("127.0.0.1", unused_tcp_port)
    await listener.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{unused_tcp_port}") as client:
            assert (await client.get("/metrics")).status_code == 404
    finally:
        await listener.stop()


async def test_direct_listener_rejects_invalid_or_multiworker_configuration(monkeypatch):
    for port in (0, 8000, 65536):
        with pytest.raises(ValueError, match="METRICS_LISTEN_PORT"):
            MetricsListener("127.0.0.1", port)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    listener = MetricsListener("127.0.0.1", 9464)
    with pytest.raises(RuntimeError, match="one backend worker"):
        await listener.start()
