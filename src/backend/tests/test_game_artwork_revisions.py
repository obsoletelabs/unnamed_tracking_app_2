"""Artwork is revalidated against revisions derived from the bytes actually served."""

import hashlib
import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from src.core.auth import get_current_user
from src.helpers import artwork_revisions, save_game_asset
from src.helpers.artwork_revisions import (
    IMMUTABLE_CACHE_CONTROL,
    MUTABLE_CACHE_CONTROL,
    matches_if_none_match,
)
from src.main import app
from tests.test_game_files_flow import game_flow  # noqa: F401


def png(colour: str, size: tuple[int, int] = (64, 96)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


async def upload(flow, kind, colour):
    response = await flow.client.post(
        f"{flow.game}/assets/{kind}", files={"file": ("a.png", png(colour), "image/png")}
    )
    assert response.status_code == 200, response.text
    return response.json()


def stored_digest(flow, kind):
    path = flow.tmp / str(flow.user_id) / "games" / "Flow" / f"{kind}.png"
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_revision_is_the_hash_of_served_bytes_and_304_repeats_headers(flow):
    saved = await upload(flow, "key_art", "red")
    url = f"{flow.game}/assets/key_art"
    first = await flow.client.get(url)
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.headers["etag"] == f'"{hashlib.sha256(first.content).hexdigest()[:32]}"'
    assert saved["revision"] == first.headers["etag"].strip('"')
    assert first.headers["cache-control"] == MUTABLE_CACHE_CONTROL
    assert "max-age=3600" not in first.headers["cache-control"]

    again = await flow.client.get(url, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["etag"] == first.headers["etag"]
    assert again.headers["cache-control"] == first.headers["cache-control"]
    weak = await flow.client.get(url, headers={"If-None-Match": f"W/{first.headers['etag']}"})
    assert weak.status_code == 304
    other = await flow.client.get(url, headers={"If-None-Match": '"stale", "other"'})
    assert other.status_code == 200


async def test_versioned_url_is_immutable_only_for_the_current_revision(flow):
    saved = await upload(flow, "key_art", "red")
    url = f"{flow.game}/assets/key_art"
    current = await flow.client.get(url, params={"v": saved["revision"]})
    assert current.status_code == 200
    assert current.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL
    assert (
        await flow.client.get(
            url, params={"v": saved["revision"]}, headers={"If-None-Match": current.headers["etag"]}
        )
    ).headers["cache-control"] == IMMUTABLE_CACHE_CONTROL

    # a stale or invented version never earns an immutable lifetime and still serves current bytes
    stale = await flow.client.get(url, params={"v": "0" * 32})
    assert stale.status_code == 200
    assert stale.content == current.content
    assert stale.headers["cache-control"] == MUTABLE_CACHE_CONTROL

    replaced = await upload(flow, "key_art", "blue")
    assert replaced["revision"] != saved["revision"]
    old_url = await flow.client.get(url, params={"v": saved["revision"]})
    assert old_url.headers["cache-control"] == MUTABLE_CACHE_CONTROL
    assert old_url.headers["etag"] == f'"{replaced["revision"]}"'


async def test_two_clients_observe_replacement_without_sharing_anything(flow):
    url = f"{flow.game}/assets/banner"
    await upload(flow, "banner", "red")
    # two devices each remember only what the server told them
    device_a = (await flow.client.get(url)).headers["etag"]
    device_b = (await flow.client.get(url)).headers["etag"]
    assert device_a == device_b
    assert (await flow.client.get(url, headers={"If-None-Match": device_b})).status_code == 304

    await upload(flow, "banner", "green")
    for etag in (device_a, device_b):
        refreshed = await flow.client.get(url, headers={"If-None-Match": etag})
        assert refreshed.status_code == 200
        assert refreshed.headers["etag"] != etag
        assert (
            hashlib.sha256(refreshed.content)
            .hexdigest()
            .startswith(refreshed.headers["etag"].strip('"'))
        )
        assert Image.open(io.BytesIO(refreshed.content)).getpixel((5, 5))[:3] == (0, 128, 0)


async def test_replacement_changes_every_variant_including_unusual_widths(flow):
    url = f"{flow.game}/assets/banner"
    await upload(flow, "banner", "red")
    widths = (1920, 800, 777, 100)
    before = {}
    for width in widths:
        response = await flow.client.get(url, params={"w": width})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        before[width] = response.headers["etag"]
        assert (
            await flow.client.get(
                url, params={"w": width}, headers={"If-None-Match": before[width]}
            )
        ).status_code == 304
    assert len(set(before.values())) == len(widths)

    saved = await upload(flow, "banner", "blue")
    for width in widths:
        after = await flow.client.get(
            url, params={"w": width}, headers={"If-None-Match": before[width]}
        )
        assert after.status_code == 200, width
        assert after.headers["etag"] != before[width]
        pixel = Image.open(io.BytesIO(after.content)).getpixel((5, 5))
        assert pixel[2] > 200 and pixel[0] < 60, (width, pixel)
        # the variant's validator follows its own bytes; its URL version is the source's
        immutable = await flow.client.get(url, params={"w": width, "v": saved["revision"]})
        assert immutable.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL
    full = await flow.client.get(url)
    assert full.headers["etag"] == f'"{saved["revision"]}"'
    assert saved["revision"] == stored_digest(flow, "banner")[:32]


async def test_downloaded_replacement_reports_new_revision(flow, monkeypatch):
    from src.api.routes import game_assets

    first = await upload(flow, "icon", "red")
    monkeypatch.setattr(game_assets, "download_image", lambda *_a, **_k: png("blue"))
    response = await flow.client.post(
        f"{flow.game}/assets/icon/from-url", json={"url": "https://example.test/a.png"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] != first["revision"]
    served = await flow.client.get(f"{flow.game}/assets/icon")
    assert served.headers["etag"] == f'"{response.json()["revision"]}"'


async def test_default_cover_revalidates_and_changes_when_art_arrives(flow):
    url = f"{flow.game}/assets/key_art"
    (flow.tmp / str(flow.user_id) / "games" / "Flow").mkdir(parents=True, exist_ok=True)
    placeholder = await flow.client.get(url)
    assert placeholder.headers["content-type"].startswith("image/svg+xml")
    assert placeholder.headers["cache-control"] == "private, no-cache"
    etag = placeholder.headers["etag"]
    assert etag == f'"{artwork_revisions.bytes_revision(placeholder.content)}"'
    unchanged = await flow.client.get(url, headers={"If-None-Match": etag})
    assert unchanged.status_code == 304
    assert unchanged.headers["etag"] == etag
    assert unchanged.headers["cache-control"] == "private, no-cache"
    # a version never makes a generated cover immutable
    guessed = await flow.client.get(url, params={"v": etag.strip('"')})
    assert guessed.headers["cache-control"] == "private, no-cache"

    await upload(flow, "key_art", "red")
    stored = await flow.client.get(url, headers={"If-None-Match": etag})
    assert stored.status_code == 200
    assert stored.headers["content-type"] == "image/png"
    assert stored.headers["etag"] != etag
    # and back to the same generated cover once the stored file is gone
    (flow.tmp / str(flow.user_id) / "games" / "Flow" / "key_art.png").unlink()
    assert (await flow.client.get(url)).headers["etag"] == etag


async def test_missing_unsupported_and_other_users_artwork_stay_safe(flow):
    assert (await flow.client.get(f"{flow.game}/assets/banner")).status_code == 404
    assert (await flow.client.get(f"{flow.game}/assets/nope")).status_code in {400, 422}
    await upload(flow, "banner", "red")
    etag = (await flow.client.get(f"{flow.game}/assets/banner")).headers["etag"]
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    try:
        for params in ({}, {"w": 800}, {"v": etag.strip('"')}):
            denied = await flow.client.get(
                f"{flow.game}/assets/banner", params=params, headers={"If-None-Match": etag}
            )
            assert denied.status_code == 404
            assert "etag" not in denied.headers
    finally:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=flow.user_id)


async def test_revision_cache_notices_same_size_replacement(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"aaaa")
    first = artwork_revisions.file_revision(path)
    assert artwork_revisions.file_revision(path) == first
    replacement = tmp_path / "b.bin"
    replacement.write_bytes(b"bbbb")
    replacement.replace(path)
    assert artwork_revisions.file_revision(path) != first


def test_if_none_match_parsing():
    etag = '"abc"'
    assert matches_if_none_match('"x", W/"abc"', etag)
    assert matches_if_none_match("*", etag)
    assert not matches_if_none_match(None, etag)
    assert not matches_if_none_match('"abcd"', etag)
    assert not matches_if_none_match("abc", etag)
