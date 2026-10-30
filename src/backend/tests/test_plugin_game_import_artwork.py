"""Plugin API 1.1.19 game import release dates and artwork use the host's validated path."""

import io
import socket
from datetime import date
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from src.database.models.game import Game
from src.database.session import SessionLocal
from src.helpers import save_game_asset
from src.helpers.remote_images import RemoteImageError
from src.plugin_api import game_import, gateway
from src.plugin_api.game_import import dispatch_game_import
from tests.test_game_files_flow import game_flow  # noqa: F401
from tests.test_plugin_game_import import import_games, payload
from tests.test_remote_asset_security import network  # noqa: F401


def picture(colour="orange"):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


@pytest.fixture(name="download")
def fake_download(monkeypatch):
    download = Mock(return_value=picture())
    monkeypatch.setattr(game_import, "download_image", download)
    return download


async def saved_game(game_id):
    async with SessionLocal() as db:
        return await db.get(Game, UUID(game_id))


def asset_path(tmp_path, game, kind):
    return (
        tmp_path
        / str(game.user_id)
        / "games"
        / game.folder_location
        / save_game_asset.ASSET_FILENAMES[kind]
    )


async def test_release_date_and_artwork_are_saved_into_the_game_folder(flow, tmp_path, download):
    result = (
        await import_games(
            flow.user_id,
            payload(
                release_date="2019-12-05",
                key_art_url="https://cdn.example/cover.png",
                banner_url="https://cdn.example/hero.png",
                logo_url="https://cdn.example/logo.png",
                icon_url="http://cdn.example/icon.png",
            ),
        )
    )["games"][0]
    assert result["created"] and "artwork_failed" not in result
    game = await saved_game(result["id"])
    assert game.release_date == date(2019, 12, 5)
    for kind in ("key_art", "banner", "logo", "icon"):
        with Image.open(asset_path(tmp_path, game, kind)) as saved:
            assert saved.size == save_game_asset.ASSET_SIZES[kind]
    assert sorted(call.args[0] for call in download.call_args_list) == [
        "http://cdn.example/icon.png",
        "https://cdn.example/cover.png",
        "https://cdn.example/hero.png",
        "https://cdn.example/logo.png",
    ]
    assert all(call.kwargs["max_bytes"] > 0 for call in download.call_args_list)


async def test_existing_pictures_and_locked_or_omitted_dates_are_kept(flow, tmp_path, download):
    first = (await import_games(flow.user_id, payload(release_date="2019-12-05")))["games"][0]
    game = await saved_game(first["id"])
    cover = asset_path(tmp_path, game, "key_art")
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"user upload")
    result = (
        await import_games(
            flow.user_id,
            payload(
                key_art_url="https://cdn.example/cover.png",
                banner_url="https://cdn.example/hero.png",
            ),
        )
    )["games"][0]
    assert [call.args[0] for call in download.call_args_list] == ["https://cdn.example/hero.png"]
    assert cover.read_bytes() == b"user upload" and "artwork_failed" not in result
    assert (await saved_game(first["id"])).release_date == date(2019, 12, 5)
    async with SessionLocal() as db:
        game = await db.get(Game, UUID(first["id"]))
        game.release_date, game.locked_fields = date(2020, 1, 1), ["release_date"]
        await db.commit()
    await import_games(flow.user_id, payload(release_date="2021-03-04"))
    assert (await saved_game(first["id"])).release_date == date(2020, 1, 1)
    # Repeated syncs never fetch a picture the game already has.
    download.reset_mock()
    await import_games(flow.user_id, payload(banner_url="https://cdn.example/other.png"))
    download.assert_not_called()


async def test_keep_existing_rechecks_under_the_row_lock(flow, tmp_path):
    result = (await import_games(flow.user_id, payload()))["games"][0]
    game = await saved_game(result["id"])
    icon = asset_path(tmp_path, game, "icon")
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(b"user upload")
    output = await save_game_asset.save_game_asset(
        picture("blue"), game.id, "icon", keep_existing=True
    )
    assert output == icon and icon.read_bytes() == b"user upload"


async def test_failed_downloads_are_reported_without_failing_the_import(flow, monkeypatch):
    monkeypatch.setattr(game_import, "download_image", Mock(side_effect=RemoteImageError("404")))
    result = (
        await import_games(
            flow.user_id,
            payload(
                icon_url="https://cdn.example/icon.png",
                key_art_url="https://cdn.example/cover.png",
                release_date="2019-12-05",
            ),
        )
    )["games"][0]
    assert result["created"] and result["artwork_failed"] == ["key_art", "icon"]
    assert (await saved_game(result["id"])).release_date == date(2019, 12, 5)


@pytest.mark.parametrize("private", [False, True])
async def test_artwork_uses_the_public_image_boundary(
    flow,
    tmp_path,
    network,  # noqa: F811
    monkeypatch,
    private,
):
    if private:
        # A public-looking name that resolves to the local network is refused before connecting.
        public_dns = socket.getaddrinfo

        def rebinding_dns(host, port=None, **kwargs):
            if host != "rebind.example":
                return public_dns(host, port, **kwargs)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port or 443))]

        monkeypatch.setattr(socket, "getaddrinfo", rebinding_dns)
    result = (
        await import_games(flow.user_id, payload(key_art_url="https://rebind.example/cover.png"))
    )["games"][0]
    game = await saved_game(result["id"])
    assert asset_path(tmp_path, game, "key_art").is_file() is not private
    if private:
        assert result["artwork_failed"] == ["key_art"] and network.calls == []
    else:
        assert network.calls[0][1] == "rebind.example"


@pytest.mark.parametrize(
    "item",
    [
        {"key_art_url": "ftp://cdn.example/cover.png"},
        {"key_art_url": "file:///etc/passwd"},
        {"key_art_url": "https://user:secret@cdn.example/cover.png"},
        {"key_art_url": "https://cdn.example/cover.png#fragment"},
        {"key_art_url": "https://cdn.example:8443/cover.png"},
        {"key_art_url": "https://cdn.example/cover image.png"},
        {"key_art_url": "https://localhost/cover.png"},
        {"banner_url": "http://127.0.0.1/hero.png"},
        {"banner_url": "http://[::1]/hero.png"},
        {"logo_url": "http://10.0.0.5/logo.png"},
        {"icon_url": "https://nas.lan/icon.png"},
        {"icon_url": "https://intranet/icon.png"},
        {"icon_url": "https://cdn.example/" + "x" * 2048},
        {"icon_url": ""},
        {"icon_url": 42},
        {"release_date": "2019-12-5"},
        {"release_date": "20191205"},
        {"release_date": "2019-02-30"},
        {"release_date": "1799-12-31"},
        {"release_date": "3001-01-01"},
        {"release_date": "2019-12-05T00:00:00"},
        {"release_date": 2019},
        {"cover_url": "https://cdn.example/cover.png"},
        {"background_url": "https://cdn.example/hero.png"},
        {"release_year": 2019},
    ],
)
async def test_invalid_dates_urls_and_unknown_fields_are_rejected_before_database_work(item):
    with pytest.raises(ValidationError):
        await dispatch_game_import(
            object(), plugin_id="test.import", user_id=uuid4(), payload=payload(**item)
        )


async def test_artwork_requires_the_live_write_grant(flow, monkeypatch, download):
    monkeypatch.setattr(gateway, "has_capability_grant", AsyncMock(return_value=False))
    async with SessionLocal() as db:
        arguments = dict(
            plugin_id="test.import",
            installation_id=uuid4(),
            user_id=flow.user_id,
            method="games.import",
            payload=payload(key_art_url="https://cdn.example/cover.png", release_date="2019-12-05"),
        )
        with pytest.raises(PermissionError, match="requires capability games.write"):
            await gateway.dispatch_gateway_request(db, **arguments, capability="games.read")
        with pytest.raises(PermissionError, match="has not been granted"):
            await gateway.dispatch_gateway_request(db, **arguments, capability="games.write")
    download.assert_not_called()
