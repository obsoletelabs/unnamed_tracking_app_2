"""Cache telemetry has bounded labels and does not change resize/cache behavior."""

import asyncio
import os

import pytest
from PIL import Image

from src.core import metrics
from src.core.cache_metrics import cache_eviction, cache_refresh, cache_request
from src.helpers.asset_previews import ensure_preview, invalidate_previews, preview_path


def sample(name, **labels):
    return metrics.REGISTRY.get_sample_value(name, labels) or 0


def test_preview_miss_hit_stale_bypass_and_invalidation(tmp_path):
    source = tmp_path / "private-game.png"
    target = preview_path(tmp_path, "private-id", "key_art", 20)
    Image.new("RGB", (60, 90), "blue").save(source)
    before = {
        outcome: sample("uta_cache_requests_total", cache_kind="artwork_preview", outcome=outcome)
        for outcome in ("miss", "hit", "stale", "bypass")
    }
    removed = sample(
        "uta_cache_evictions_total", cache_kind="artwork_preview", reason="invalidation"
    )
    assert ensure_preview(source, target, 20)[0] == target
    assert ensure_preview(source, target, 20)[0] == target
    os.utime(target, (0, 0))
    assert ensure_preview(source, target, 20)[0] == target
    assert ensure_preview(source, target.with_name("large.jpg"), 80)[0] == source
    for outcome in before:
        assert (
            sample("uta_cache_requests_total", cache_kind="artwork_preview", outcome=outcome)
            == before[outcome] + 1
        )
    invalidate_previews(tmp_path, "private-id", "key_art")
    assert not target.exists()
    assert (
        sample("uta_cache_evictions_total", cache_kind="artwork_preview", reason="invalidation")
        == removed + 1
    )


@pytest.mark.parametrize("mode", ["ok", "error", "cancelled", "returned_error"])
def test_refresh_outcomes_include_exception_and_cancellation(mode):
    outcome = "error" if mode == "returned_error" else mode
    before = sample(
        "uta_cache_refresh_duration_seconds_count", cache_kind="metadata_search", outcome=outcome
    )

    def refresh():
        with cache_refresh("metadata_search") as observation:
            if mode == "returned_error":
                observation.failed()
            elif mode == "error":
                raise ValueError("private failure")
            elif mode == "cancelled":
                raise asyncio.CancelledError

    if mode == "error":
        with pytest.raises(ValueError):
            refresh()
    elif mode == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            refresh()
    else:
        refresh()
    assert (
        sample(
            "uta_cache_refresh_duration_seconds_count",
            cache_kind="metadata_search",
            outcome=outcome,
        )
        == before + 1
    )


def test_unknown_labels_and_inactive_refresh_do_not_create_series():
    before = sample(
        "uta_cache_refresh_duration_seconds_count", cache_kind="metadata_search", outcome="ok"
    )
    with cache_refresh("metadata_search", active=False):
        pass
    assert (
        sample(
            "uta_cache_refresh_duration_seconds_count", cache_kind="metadata_search", outcome="ok"
        )
        == before
    )
    cache_request("private-account", "hit")
    cache_request("metadata_search", "private-query")
    cache_eviction("private-url", "invalidation")
    with cache_refresh("private-provider"):
        pass
    for family in metrics.REGISTRY.collect():
        if family.name.startswith("uta_cache"):
            for value in family.samples:
                assert all(not label.startswith("private-") for label in value.labels.values())
