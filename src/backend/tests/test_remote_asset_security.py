"""Public image fetching and the game URL boundary reject unsafe resources."""

import io
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import requests
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from src.api.routes import game_assets, game_metadata, library_sync
from src.core.auth import get_current_user
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal, get_db
from src.helpers import public_http, remote_images
from tests.test_game_files_flow import game_flow  # noqa: F401


class ImageResponse:
    def __init__(self, content, status=200, declared=None):
        self.content = content
        self.status = self.status_code = status
        self.headers = {"content-type": "image/png"}
        if declared is not None:
            self.headers["content-length"] = str(declared)
        self.position = 0

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read(self, size):
        data = self.content[self.position : self.position + size]
        self.position += len(data)
        return data


@pytest.fixture
def network(monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "orange").save(buffer, format="PNG")
    state = SimpleNamespace(
        content=buffer.getvalue(), calls=[], connections=[], status=200, declared=None, responses=[]
    )

    def dns(_host, port=None, **_kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1" if _host == "127.0.0.1" else "93.184.216.34", port or 443),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", dns)

    class Connection:
        def __init__(self, host, port, **_kwargs):
            self.host, self.port, self.closed = host, port, False
            state.connections.append(self)

        def request(self, method, path, headers):
            state.calls.append((method, self.host, self.port, path, headers))

        def getresponse(self):
            if state.responses:
                return state.responses.pop(0)
            return ImageResponse(state.content, state.status, state.declared)

        def close(self):
            self.closed = True

    monkeypatch.setattr(public_http, "HTTPConnection", Connection)
    monkeypatch.setattr(public_http, "HTTPSConnection", Connection)
    return state


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@public.example/image.png",
        "https://@public.example/image.png",
        "https://public.example/image.png#fragment",
        "https://public.example/image.png#",
        "https://public.example/\x00image.png",
        "https://public.example/\x7fimage.png",
        "https://public.example/image name.png",
        "https://public.example\\private/image.png",
        "https://public.example:81/image.png",
        "https://public.example:0/image.png",
        "https://public.example:65536/image.png",
    ],
)
def test_cached_images_refuse_unsafe_url_syntax_before_networking(network, url):
    with pytest.raises(remote_images.RemoteImageError):
        remote_images.download_image(url)
    assert network.calls == []


def test_shared_address_space_is_not_a_public_image_host(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", 443))
        ],
    )
    assert remote_images.is_public_host("shared.example") is False


@pytest.mark.parametrize(
    "private", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::ffff:127.0.0.1"]
)
def test_mixed_dns_answers_are_refused_before_connecting(network, monkeypatch, private):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (private, 443)),
        ],
    )
    with pytest.raises(remote_images.RemoteImageError):
        remote_images.download_image("https://public.example/image.png")
    assert network.calls == []


def test_pinned_connection_does_not_resolve_again_and_retains_tls_identity(monkeypatch):
    resolutions, connected = [], []

    def dns(host, port, **_kwargs):
        resolutions.append((host, port))
        address = "93.184.216.34" if len(resolutions) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    class Socket:
        def __init__(self, *_args):
            pass

        def settimeout(self, _timeout):
            pass

        def connect(self, address):
            connected.append(address)

        def close(self):
            pass

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(socket, "socket", Socket)
    connection = public_http.public_connection("https://public.example/image.png")
    connection._create_connection((connection.host, connection.port))
    assert resolutions == [("public.example", 443)]
    assert connected == [("93.184.216.34", 443)]
    assert connection.host == "public.example"
    assert connection._context.check_hostname is True
    assert connection._context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("case", ["redirect", "declared_size", "streamed_size", "not_image"])
def test_download_is_bounded_and_requires_real_image_content(network, case):
    if case == "redirect":
        network.status = 302
    elif case == "declared_size":
        network.declared = remote_images.MAX_BYTES + 1
    elif case == "streamed_size":
        network.content = b"x" * (remote_images.MAX_BYTES + 1)
    else:
        network.content = b"<html>not a PNG</html>"
    with pytest.raises(remote_images.RemoteImageError):
        remote_images.download_image("https://public.example/image.png")
    assert len(network.calls) <= 1
    assert all(connection.closed for connection in network.connections)


async def test_game_asset_url_route_uses_the_same_public_network_policy(
    network, monkeypatch, tmp_path
):
    game_id, user_id = uuid4(), uuid4()
    owner = AsyncMock(return_value=SimpleNamespace(id=game_id, user_id=user_id))
    save = AsyncMock(return_value=tmp_path / "key_art.png")
    monkeypatch.setattr(game_assets, "_get_game_or_404", owner)
    monkeypatch.setattr(game_assets, "get_max_upload_size_mb", AsyncMock(return_value=15))
    monkeypatch.setattr(game_assets, "save_game_asset", save)
    app = FastAPI()
    app.include_router(game_assets.router, prefix="/api/game")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = f"/api/game/{game_id}/assets/key_art/from-url"
        response = await client.post(path, json={"url": "http://127.0.0.1/private.png"})
        assert response.status_code == 400
        save.assert_not_awaited()
        assert network.calls == []
        response = await client.post(path, json={"url": "https://public.example/image.png"})
        assert response.status_code == 200
        assert save.await_args.args == (network.content, game_id, "key_art")
        assert owner.await_args.args[0] == game_id and owner.await_args.args[2] == user_id


def redirect_response(location):
    response = ImageResponse(b"", status=302)
    response.headers["location"] = location
    return response


@pytest.mark.parametrize("location", ["/next.png", "https://cdn.example/next.png?sig=a%2Bb"])
def test_public_image_redirects_preserve_signed_queries_and_close_connections(network, location):
    network.responses = [redirect_response(location)]
    assert remote_images.download_image("https://public.example/image.png") == network.content
    assert len(network.calls) == 2
    assert network.calls[1][1] == (
        "cdn.example" if location.startswith("https:") else "public.example"
    )
    assert network.calls[1][3] == ("/next.png?sig=a%2Bb" if "?" in location else "/next.png")
    assert all(connection.closed for connection in network.connections)
    assert all(
        set(call[4]) == {"User-Agent", "Accept", "Accept-Encoding"} for call in network.calls
    )


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/private.png",
        "https://user:secret@cdn.example/image.png",
        "https://cdn.example:81/image.png",
        "https://cdn.example/image.png#fragment",
        " https://cdn.example/image.png",
        "\nhttps://cdn.example/image.png",
    ],
)
def test_redirect_targets_are_checked_before_the_next_connection(network, location):
    network.responses = [redirect_response(location)]
    with pytest.raises(remote_images.RemoteImageError):
        remote_images.download_image("https://public.example/image.png")
    assert len(network.calls) == 1
    assert len(network.connections) == 1
    assert network.connections[0].closed


def test_redirect_rechecks_dns_even_for_the_same_hostname(network, monkeypatch):
    resolutions = []

    def dns(host, port, **_kwargs):
        resolutions.append(host)
        address = "93.184.216.34" if len(resolutions) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    network.responses = [redirect_response("/next.png")]
    with pytest.raises(remote_images.RemoteImageError):
        remote_images.download_image("https://public.example/image.png")
    assert resolutions == ["public.example", "public.example"]
    assert len(network.calls) == 1
    assert network.connections[0].closed


def test_redirect_loops_stop_at_the_limit_and_close_every_connection(network):
    network.responses = [redirect_response("/again.png") for _ in range(10)]
    with pytest.raises(remote_images.RemoteImageError, match="too many image redirects"):
        remote_images.download_image("https://public.example/image.png")
    assert len(network.calls) == remote_images.MAX_REDIRECTS + 1
    assert all(connection.closed for connection in network.connections)


def test_pixel_limit_is_enforced_before_image_conversion(network, monkeypatch):
    monkeypatch.setattr(remote_images, "MAX_PIXELS", 63)
    with pytest.raises(remote_images.RemoteImageError, match="pixel limit"):
        remote_images.download_image("https://public.example/image.png")
    assert network.connections[0].closed


def test_valid_signature_does_not_make_a_corrupt_png_acceptable(network):
    network.content = network.content[:40]
    with pytest.raises(remote_images.RemoteImageError, match="not a valid image"):
        remote_images.download_image("https://public.example/image.png")
    assert network.connections[0].closed


def test_configured_byte_limit_is_enforced_while_streaming(network):
    with pytest.raises(remote_images.RemoteImageError, match="too large"):
        remote_images.download_image("https://public.example/image.png", max_bytes=10)
    assert network.connections[0].closed


def test_connection_falls_back_only_to_prevalidated_addresses_and_closes_failed_sockets(
    monkeypatch,
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
        ],
    )
    sockets = []

    class Socket:
        def __init__(self, *_args):
            self.closed = False
            self.address = None
            sockets.append(self)

        def settimeout(self, _timeout):
            pass

        def connect(self, address):
            self.address = address
            if len(sockets) == 1:
                raise OSError("first address unavailable")

        def close(self):
            self.closed = True

    monkeypatch.setattr(socket, "socket", Socket)
    connection = public_http.public_connection("https://public.example/image.png")
    result = connection._create_connection((connection.host, connection.port))
    assert len(sockets) == 2
    assert sockets[0].address == ("93.184.216.34", 443)
    assert sockets[0].closed is True
    assert result is sockets[1]
    assert result.address == ("2606:4700:4700::1111", 443, 0, 0)
    result.close()


@pytest.mark.parametrize("private", [False, True])
async def test_library_enrichment_uses_the_public_image_boundary(network, monkeypatch, private):
    legacy_response = SimpleNamespace(
        content=network.content,
        headers={"content-type": "image/png"},
        raise_for_status=lambda: None,
    )
    legacy_calls = []

    def legacy_get(*args, **_kwargs):
        legacy_calls.append(args)
        return legacy_response

    monkeypatch.setattr(requests, "get", legacy_get)
    save = AsyncMock()
    monkeypatch.setattr(library_sync, "save_locked_game_asset", save)
    game = SimpleNamespace(id=uuid4())
    url = "http://127.0.0.1/private.png" if private else "https://public.example/art.png"
    saved = await library_sync._download_asset(url, game, "icon")
    assert saved is (not private)
    assert legacy_calls == []
    if private:
        save.assert_not_awaited()
        assert network.calls == []
    else:
        assert save.await_args.args == (network.content, game, "icon")
        assert network.calls[0][1] == "public.example"


@pytest.mark.parametrize("kind", ["key_art", "banner"])
@pytest.mark.parametrize("private", [False, True])
async def test_metadata_refresh_uses_public_image_validation_for_both_art_kinds(
    flow, network, monkeypatch, kind, private
):
    legacy_calls = []

    def legacy_get(*args, **_kwargs):
        legacy_calls.append(args)
        return SimpleNamespace(content=network.content, raise_for_status=lambda: None)

    monkeypatch.setattr(requests, "get", legacy_get)
    save = AsyncMock()
    monkeypatch.setattr(game_metadata, "save_locked_game_asset", save)
    url = "http://127.0.0.1/private.png" if private else "https://public.example/art.png"
    monkeypatch.setattr(
        game_metadata,
        "resolve_library_record",
        AsyncMock(return_value=({"title": "Flow", "assets": [{"kind": kind, "url": url}]}, [])),
    )
    response = await flow.client.post(
        f"{flow.game}/metadata/refresh",
        json={
            "dry_run": False,
            "update_text": False,
            "fill_missing_art": True,
            "overwrite_existing_art": True,
        },
    )
    assert response.status_code == 200, response.text
    assert legacy_calls == []
    if private:
        save.assert_not_awaited()
        assert len(response.json()["provider_errors"]) == 1
        assert network.calls == []
    else:
        assert save.await_args.args[0] == network.content
        assert save.await_args.args[1].id == flow.game_id
        assert save.await_args.args[2] == kind
        assert response.json()["provider_errors"] == []


async def test_library_enrichment_keeps_art_fallback_and_uses_configured_limit(
    flow, network, monkeypatch
):
    save = AsyncMock()
    limit = AsyncMock(return_value=1)
    monkeypatch.setattr(library_sync, "save_locked_game_asset", save)
    monkeypatch.setattr(library_sync, "get_max_upload_size_mb", limit)
    monkeypatch.setattr(
        library_sync,
        "resolve_library_record",
        AsyncMock(
            return_value=(
                {
                    "title": "Flow",
                    "assets": [
                        {"kind": "icon", "url": "http://127.0.0.1/private.png"},
                        {"kind": "icon", "url": "https://public.example/icon.png"},
                    ],
                },
                [],
            )
        ),
    )
    async with SessionLocal() as db:
        game = await db.scalar(select(Game).where(Game.id == flow.game_id).with_for_update())
        await library_sync._enrich_new_game(game, await db.get(User, flow.user_id), {})
        save.assert_awaited_once()
        assert save.await_args.args == (network.content, game, "icon")
        assert len(network.calls) == 1 and network.calls[0][1] == "public.example"
        limit.assert_awaited_once()

        save.reset_mock()
        network.declared = 2 * 1024 * 1024
        await library_sync._enrich_new_game(game, await db.get(User, flow.user_id), {})
        save.assert_not_awaited()
