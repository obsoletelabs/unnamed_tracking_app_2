"""Owned public uploads reject spoofed content and preserve usable file names."""

import io
from pathlib import Path
from urllib.parse import unquote

import pytest
from PIL import Image

from src.api.routes import media
from src.helpers.media import classify_media, safe_filename
from tests.test_game_files_flow import _png, _wav, game_flow

flow = game_flow


async def test_excessive_numeric_ranges_return_a_safe_416_instead_of_a_server_error(flow):
    response = await flow.client.post(
        f"{flow.game}/screenshots", files={"file": ("capture.png", _png(), "image/png")}
    )
    name = response.json()["results"][0]["filename"]
    response = await flow.client.get(
        f"{flow.game}/screenshots/screenshot/{name}",
        headers={"Range": "bytes=" + "9" * 5000 + "-"},
    )
    assert response.status_code == 416
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"


async def test_date_redetection_uses_the_preserved_name_after_storage_truncation(flow):
    name = "capture_" + "a" * 230 + "_20260102_030405.png"
    response = await flow.client.post(
        f"{flow.game}/screenshots", files={"file": (name, _png(), "image/png")}
    )
    assert response.json()["results"][0]["status"] == "saved"
    item = (await flow.client.get(f"{flow.game}/screenshots")).json()["media"][0]
    assert item["original_filename"] == name
    assert "20260102" not in item["filename"]
    await flow.client.patch(f"{flow.game}/screenshots/{item['id']}", json={"taken_at": None})
    response = await flow.client.post(
        f"{flow.game}/screenshots/detect-dates", json={"ids": [item["id"]]}
    )
    restored = response.json()["media"][0]
    assert restored["taken_at"] == item["taken_at"] and restored["taken_source"] == "filename"


@pytest.mark.parametrize(
    "extension,format_name",
    [
        ("png", "PNG"),
        ("jfif", "JPEG"),
        ("gif", "GIF"),
        ("webp", "WEBP"),
        ("avif", "AVIF"),
        ("bmp", "BMP"),
        ("tiff", "TIFF"),
        ("heic", "HEIF"),
        ("ico", "ICO"),
    ],
)
async def test_real_supported_raster_formats_save_with_untrusted_mime(flow, extension, format_name):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "orange").save(buffer, format=format_name)
    response = await flow.client.post(
        f"{flow.game}/screenshots",
        files={"file": (f"capture.{extension}", buffer.getvalue(), "application/octet-stream")},
    )
    assert response.json()["results"][0]["status"] == "saved", response.text


@pytest.mark.parametrize("range_header,expected", [(None, 200), ("bytes=12-19", 206)])
async def test_audio_downloads_and_seeking_preserve_the_original_bytes(
    flow, range_header, expected
):
    response = await flow.client.post(
        f"{flow.game}/screenshots", files={"file": ("original.wav", _wav(), "audio/wav")}
    )
    name = response.json()["results"][0]["filename"]
    response = await flow.client.get(
        f"{flow.game}/screenshots/soundtrack/{name}",
        headers={"Range": range_header} if range_header else {},
    )
    assert response.status_code == expected
    assert response.content == (_wav()[12:20] if range_header else _wav())
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_existing_storage_only_files_remain_available_without_an_original_name(flow):
    directory = flow.tmp / str(flow.user_id) / "games" / "Flow" / "screenshots"
    directory.mkdir(parents=True)
    (directory / "abcdef12_existing.png").write_bytes(_png())
    response = await flow.client.get(f"{flow.game}/screenshots/screenshot/abcdef12_existing.png")
    assert response.status_code == 200
    assert response.content == _png()
    assert response.headers["content-security-policy"] == "sandbox"
    assert "existing.png" in response.headers["content-disposition"]


async def test_oversized_media_is_rejected_before_content_inspection_or_storage(flow, monkeypatch):
    from unittest.mock import AsyncMock

    from src.api.routes import game_assets

    monkeypatch.setattr(game_assets, "get_max_upload_size_mb", AsyncMock(return_value=1))
    response = await flow.client.post(
        f"{flow.game}/screenshots",
        files={"file": ("huge.png", b"x" * (1024 * 1024 + 100), "image/png")},
    )
    result = response.json()["results"][0]
    assert result["status"] == "rejected" and result["reason"] == "Larger than 1 MB."
    assert list(flow.tmp.rglob("*")) == []


@pytest.mark.parametrize("target", ["game", "inbox"])
@pytest.mark.parametrize(
    "name,data,mime",
    [
        ("page.html", b"<script>alert(1)</script>", "image/png"),
        ("page.svg", b'<svg onload="alert(1)"/>', "image/svg+xml"),
        ("fake.png", b"<html>not an image</html>", "image/png"),
        ("fake.mp4", b"<html>not a video</html>", "video/mp4"),
        ("fake.mp3", b"ID3" + b"\x00" * 64, "audio/mpeg"),
        ("wrong.jpg", _png(), "image/jpeg"),
    ],
)
async def test_spoofed_media_is_rejected_without_a_file_or_row(
    flow, monkeypatch, target, name, data, mime
):
    monkeypatch.setattr(media, "_USER_DATA_ROOT", flow.tmp / "user")
    endpoint = f"{flow.game}/screenshots" if target == "game" else "/api/media/inbox"
    response = await flow.client.post(endpoint, files={"file": (name, data, mime)})
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "rejected"
    assert list(flow.tmp.rglob("*")) == []
    listed = (await flow.client.get(endpoint)).json()["media"]
    assert listed == []


async def test_bad_file_does_not_discard_good_files_in_the_same_batch(flow):
    response = await flow.client.post(
        f"{flow.game}/screenshots",
        files=[
            ("files", ("valid.png", _png(), "application/octet-stream")),
            ("files", ("invalid.png", b"<html>bad</html>", "image/png")),
        ],
    )
    assert [item["status"] for item in response.json()["results"]] == ["saved", "rejected"]
    listed = (await flow.client.get(f"{flow.game}/screenshots")).json()["media"]
    assert len(listed) == 1


@pytest.mark.parametrize(
    "range_header,expected", [(None, 200), ("bytes=0-9", 206), ("bytes=999999-", 416)]
)
async def test_stored_media_has_equivalent_safe_headers_for_whole_and_range_responses(
    flow, range_header, expected
):
    response = await flow.client.post(
        f"{flow.game}/screenshots", files={"file": ("capture.png", _png(), "image/png")}
    )
    name = response.json()["results"][0]["filename"]
    response = await flow.client.get(
        f"{flow.game}/screenshots/screenshot/{name}",
        headers={"Range": range_header} if range_header else {},
    )
    assert response.status_code == expected
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["accept-ranges"] == "bytes"
    if expected == 200:
        assert response.content == _png()
        assert response.headers["content-type"] == "image/png"
    elif expected == 206:
        assert response.content == _png()[:10]
        assert response.headers["content-range"] == f"bytes 0-9/{len(_png())}"
    else:
        assert response.headers["content-range"] == f"bytes */{len(_png())}"


async def test_unicode_original_name_survives_inbox_assignment_title_changes_and_download(
    flow, monkeypatch
):
    monkeypatch.setattr(media, "_USER_DATA_ROOT", flow.tmp / "user")
    monkeypatch.setattr(media, "GAMES_DATA_ROOT", flow.tmp)
    monkeypatch.setattr(media, "create_game_folder", lambda *_args: None)
    original = "東京 — boss encounter.png"
    response = await flow.client.post(
        "/api/media/inbox", files={"file": (original, _png(), "image/png")}
    )
    name = response.json()["results"][0]["filename"]
    assert name.isascii() and len(name.encode()) <= 255
    inbox = (await flow.client.get("/api/media/inbox")).json()["media"][0]
    assert inbox.get("original_filename") == original
    downloaded = await flow.client.get(inbox["url"])
    assert original in unquote(downloaded.headers["content-disposition"])
    response = await flow.client.post(
        f"/api/media/inbox/screenshot/{name}/assign", json={"game_id": str(flow.game_id)}
    )
    assert response.status_code == 200, response.text
    item = (await flow.client.get(f"{flow.game}/screenshots")).json()["media"][0]
    assert item["original_filename"] == original
    response = await flow.client.patch(
        f"{flow.game}/screenshots/{item['id']}", json={"title": "A new label"}
    )
    assert response.json()["title"] == "A new label"
    assert response.json()["original_filename"] == original
    response = await flow.client.get(item["url"], headers={"Range": "bytes=0-9"})
    assert response.status_code == 206
    assert original in unquote(response.headers["content-disposition"])
    assert (await flow.client.get("/api/media")).json()[0]["original_filename"] == original


@pytest.mark.parametrize(
    "name", ["a" * 600 + ".png", "..\\folder\\capture.png", "../../capture.png"]
)
def test_storage_names_are_bounded_and_never_keep_client_path_components(name):
    stored = safe_filename(name)
    assert len(stored.encode()) <= 255
    assert "folder" not in stored
    assert Path(stored).suffix == ".png"


@pytest.mark.parametrize("claimed", ["image/png", "video/webm", "audio/mpeg"])
def test_sender_mime_cannot_make_an_unpermitted_extension_into_media(claimed):
    assert classify_media(claimed, "payload.html") is None


async def test_arbitrary_documents_still_download_as_attachments_with_safe_headers(flow):
    original = "Guide — 日本語.html"
    data = b"<html>an intentionally downloadable document</html>"
    response = await flow.client.post(
        f"{flow.game}/files/doc", files={"file": (original, data, "text/html")}
    )
    assert response.json()["results"][0]["status"] == "saved"
    item = (await flow.client.get(f"{flow.game}/files/doc")).json()["files"][0]
    assert item.get("original_filename") == original
    response = await flow.client.get(item["url"])
    assert response.content == data
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert original in unquote(response.headers["content-disposition"])
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"
