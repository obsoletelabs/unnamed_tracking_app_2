"""The in-app viewer reuses telemetry without dependency I/O or credential exposure."""

import asyncio
from unittest.mock import Mock

import httpx
import pytest

from src.core import metrics
from src.core.auth import get_current_admin
from src.features import metrics_viewer
from src.main import app


@pytest.fixture(autouse=True)
def reset_viewer(monkeypatch):
    monkeypatch.setattr(metrics_viewer, "_value", None)
    monkeypatch.setattr(metrics_viewer, "_pending", None)
    monkeypatch.setattr(metrics.settings, "METRICS_ENABLED", True)


async def test_viewer_is_admin_only_and_does_not_return_a_scrape_token(monkeypatch):
    monkeypatch.setattr(metrics.settings, "METRICS_TOKEN", "private-scrape-credential")
    metrics.HTTP_DURATION.labels("GET", "/viewer-test/{item}").observe(0.02)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/settings/metrics/snapshot")).status_code in {401, 403}
        app.dependency_overrides[get_current_admin] = lambda: object()
        try:
            response = await client.get("/api/settings/metrics/snapshot")
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            body = response.json()
            assert body["enabled"] and body["scope"] == "process"
            row = next(
                row
                for row in body["histograms"]["http"]
                if row["labels"]["route"] == "/viewer-test/{item}"
            )
            assert row["count"] == 1 and row["sum"] == 0.02
            assert "private-scrape-credential" not in response.text
        finally:
            app.dependency_overrides.pop(get_current_admin, None)


async def test_concurrent_snapshots_share_the_short_cache_without_scrape_probes(monkeypatch):
    for probe in metrics._snapshots:
        monkeypatch.setattr(probe, "refresh", Mock(side_effect=AssertionError("No probes")))
    original = metrics_viewer._collect
    collector = Mock(side_effect=original)
    monkeypatch.setattr(metrics_viewer, "_collect", collector)
    first, second = await asyncio.gather(metrics_viewer.snapshot(), metrics_viewer.snapshot())
    third = await metrics_viewer.snapshot()
    assert first is second is third and collector.call_count == 1


async def test_disabled_metrics_do_not_collect(monkeypatch):
    monkeypatch.setattr(metrics.settings, "METRICS_ENABLED", False)
    collector = Mock(side_effect=AssertionError("Disabled collection"))
    monkeypatch.setattr(metrics_viewer, "_collect", collector)
    assert await metrics_viewer.snapshot() == {"enabled": False}
    collector.assert_not_called()
