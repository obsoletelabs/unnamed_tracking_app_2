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


def test_viewer_reads_the_registered_cache_counter():
    """Regression for #355: the viewer must read the series the collector registers."""
    names = {sample.name for family in metrics.REGISTRY.collect() for sample in family.samples}
    assert "uta_cache_requests_total" in names
    assert "uta_cache_access_total" not in names
    assert "uta_cache_access_total" not in metrics_viewer._VALUES
    assert metrics_viewer._VALUES["uta_cache_requests_total"] == "cache_requests"
    assert "uta_cache_requests_total" in metrics_viewer._SAMPLE_NAMES


def test_cache_requests_counter_increments_on_hit_and_miss_and_is_surfaced():
    from src.core.cache_metrics import cache_request

    def count(outcome):
        rows = metrics_viewer._collect()["values"]["cache_requests"]
        return sum(
            row["value"]
            for row in rows
            if row["labels"] == {"cache_kind": "media_image", "outcome": outcome}
        )

    before = {outcome: count(outcome) for outcome in ("hit", "miss")}
    cache_request("media_image", "miss")
    cache_request("media_image", "hit")
    cache_request("media_image", "hit")
    assert count("miss") == before["miss"] + 1
    assert count("hit") == before["hit"] + 2


def test_cache_requests_labels_stay_bounded():
    from src.core.cache_metrics import cache_request

    cache_request("media_image/private-title.png", "hit")
    cache_request("media_image", "https://example.invalid/token")
    rows = metrics_viewer._collect()["values"]["cache_requests"]
    assert rows and all(set(row["labels"]) == {"cache_kind", "outcome"} for row in rows)
    assert len(rows) <= 4 * 5
    assert not any("private-title" in str(row) or "example.invalid" in str(row) for row in rows)
