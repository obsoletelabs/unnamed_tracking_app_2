"""Saving artwork fills the same cache served by the image routes."""

import asyncio
from types import SimpleNamespace

import pytest

from src.helpers import image_prefetch
from src.helpers.remote_images import RemoteImageError


@pytest.fixture
async def prefetch():
    image_prefetch.enable()
    yield
    await image_prefetch.disable()


async def test_downloads_are_deduplicated_and_cached(prefetch, monkeypatch, tmp_path):
    calls = []

    def download(url, target, width):
        calls.append((url, width))
        target.write_bytes(b"cached")

    monkeypatch.setattr(image_prefetch, "fetch_and_store", download)
    target = tmp_path / "poster.jpg"
    for _ in range(3):
        image_prefetch.schedule("https://example.test/p.jpg", target, 400)
    await asyncio.gather(*image_prefetch._tasks)
    image_prefetch.schedule("https://example.test/p.jpg", target, 400)
    assert calls == [("https://example.test/p.jpg", 400)]
    assert target.read_bytes() == b"cached"


async def test_failed_download_leaves_route_fallback_available(prefetch, monkeypatch, tmp_path):
    def fail(*_args):
        raise RemoteImageError("unavailable")

    monkeypatch.setattr(image_prefetch, "fetch_and_store", fail)
    target = tmp_path / "poster.jpg"
    image_prefetch.schedule("https://example.test/p.jpg", target, 400)
    await asyncio.gather(*image_prefetch._tasks)
    assert not target.exists()
    assert not image_prefetch._running


def test_backdrop_and_hero_share_one_cache_entry():
    row = SimpleNamespace(poster_url="poster", backdrop_url="backdrop")
    assert image_prefetch.media_image_names(row) == [("poster", "poster"), ("backdrop", "backdrop")]
    row.backdrop_url = None
    assert image_prefetch.media_image_names(row) == [("poster", "poster"), ("hero", "poster")]
