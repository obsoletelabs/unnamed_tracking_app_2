"""Notification images: policy, SSRF/redirect/size limits, malicious files and clean fallback."""

import io
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from src.core.config import settings
from src.database.models.movies import Movie
from src.database.session import SessionLocal
from src.features import notification_images as images
from src.helpers import public_http, remote_images
from src.plugin_api.notification_story_contracts import ImageCapabilities
from tests.test_notification_story_pages import notification_world, rich  # noqa: F401

URL = "https://img.example.com/a.png"


def png(size=(8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "orange").save(buffer, format="PNG")
    return buffer.getvalue()


class Reply:
    def __init__(self, content=b"", status=200, headers=None):
        self.content, self.status, self.position = content, status, 0
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read(self, size):
        data = self.content[self.position : self.position + size]
        self.position += len(data)
        return data


@pytest.fixture
def network(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_CACHE_DIR", str(tmp_path / "images"))
    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_FAILURE_SECONDS", 300)
    state = SimpleNamespace(
        replies=[], requests=[], lookups=[], private={"internal.example.com"}, default=None
    )

    def dns(host, port=None, **_kwargs):
        state.lookups.append(host)
        address = "10.0.0.5" if host in state.private else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 443))]

    class Connection:
        def __init__(self, host, port, **_kwargs):
            self.host = host

        def request(self, method, path, headers):
            state.requests.append((self.host, path, headers))

        def getresponse(self):
            return state.replies.pop(0) if state.replies else state.default

        def close(self):
            pass

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(public_http, "HTTPSConnection", Connection)
    monkeypatch.setattr(public_http, "HTTPConnection", Connection)
    state.default = Reply(png())
    return state


async def test_valid_image_is_reencoded_cached_and_served_from_disk(network):
    path = await images.cached_image(URL)
    assert path is not None and path.suffix == ".jpg"
    with Image.open(path) as stored:
        assert stored.format == "JPEG"
    requests = len(network.requests)
    assert await images.cached_image(URL) == path
    assert len(network.requests) == requests  # second view never touches the remote server
    assert network.requests[0][2]["Accept-Encoding"] == "identity"
    assert "Cookie" not in network.requests[0][2] and "Authorization" not in network.requests[0][2]


async def test_private_destination_is_refused_before_connecting_and_remembered(network):
    url = "https://internal.example.com/a.png"
    assert await images.cached_image(url) is None
    assert network.requests == []
    lookups = len(network.lookups)
    assert await images.cached_image(url) is None
    assert len(network.lookups) == lookups  # the failure is remembered, not retried


@pytest.mark.parametrize(
    "private", ["127.0.0.1", "169.254.169.254", "10.1.2.3", "192.168.0.9", "::1"]
)
async def test_every_private_range_is_refused(network, monkeypatch, private):
    family = socket.AF_INET6 if ":" in private else socket.AF_INET
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_a, **_k: [(family, socket.SOCK_STREAM, 6, "", (private, 443))],
    )
    assert await images.cached_image(f"https://{uuid4().hex}.example.com/a.png") is None
    assert network.requests == []


async def test_redirect_to_a_private_host_is_refused(network):
    network.replies = [
        Reply(status=302, headers={"location": "https://internal.example.com/x.png"})
    ]
    assert await images.cached_image(URL) is None
    assert [request[0] for request in network.requests] == ["img.example.com"]


async def test_redirect_to_a_different_scheme_or_loop_is_refused(network):
    network.replies = [Reply(status=302, headers={"location": "http://img.example.com:8080/x"})]
    assert await images.cached_image("https://img.example.com/b.png") is None
    network.default = Reply(status=302, headers={"location": "https://img.example.com/loop"})
    assert await images.cached_image("https://img.example.com/c.png") is None
    assert len(network.requests) <= 1 + remote_images.MAX_REDIRECTS + 1


async def test_declared_and_undeclared_oversize_are_refused(network, monkeypatch):
    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MAX_BYTES", 2048)
    network.replies = [Reply(png(), headers={"content-length": "99999999"})]
    assert await images.cached_image("https://img.example.com/big1.png") is None
    network.replies = [Reply(png((600, 600)))]  # no declared length: counted while streaming
    assert await images.cached_image("https://img.example.com/big2.png") is None


@pytest.mark.parametrize(
    "payload",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"<html><script>alert(1)</script></html>",
        b"GIF89a" + b"\x00" * 10,
        b"",
    ],
)
async def test_non_raster_or_broken_files_never_reach_the_cache(network, payload, tmp_path):
    network.replies = [Reply(payload)]
    assert await images.cached_image(f"https://img.example.com/{uuid4().hex}.png") is None
    assert not list((tmp_path / "images").rglob("*.jpg"))


async def test_polyglot_payload_is_destroyed_by_reencoding(network):
    network.replies = [Reply(png() + b"<script>alert(1)</script>")]
    path = await images.cached_image("https://img.example.com/poly.png")
    assert path is not None and b"<script" not in path.read_bytes()


async def test_decompression_bomb_is_refused(network, monkeypatch):
    monkeypatch.setattr(remote_images, "MAX_PIXELS", 100)
    network.replies = [Reply(png((50, 50)))]
    assert await images.cached_image("https://img.example.com/bomb.png") is None


async def test_non_200_and_timeouts_fall_back(network):
    network.replies = [Reply(status=404)]
    assert await images.cached_image("https://img.example.com/404.png") is None

    def explode(*_args, **_kwargs):
        raise TimeoutError("slow")

    network.default = SimpleNamespace(status=200, getheader=explode, read=explode)
    assert await images.cached_image("https://img.example.com/slow.png") is None


async def test_failure_marker_expires_so_images_recover(network, monkeypatch):
    network.replies = [Reply(status=500)]
    assert await images.cached_image(URL) is None
    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_FAILURE_SECONDS", 0)
    assert await images.cached_image(URL) is not None


# --- policy and routes -----------------------------------------------------------------------


def test_policy_is_narrowed_by_surface_capability(monkeypatch):
    for configured, caps, expected in (
        ("proxied", None, "proxied"),
        ("proxied", ImageCapabilities(max_images=1, external_urls=True), "external"),
        ("external", ImageCapabilities(max_images=1, proxied_urls=True), "proxied"),
        ("external", ImageCapabilities(max_images=1, external_urls=True), "external"),
        ("off", ImageCapabilities(max_images=1, external_urls=True), "off"),
    ):
        monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MODE", configured)
        assert images.effective_image_mode(caps) == expected


async def image_state(world):
    _, story_id = await world.emit()
    page = (await world.client.get(f"/api/notifications/stories/{story_id}")).json()
    return story_id, page["view"]["images"][0]


async def test_policy_selects_proxy_external_or_fallback_artwork(world, network, monkeypatch):
    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MODE", "proxied")
    story_id, image = await image_state(world)
    assert image["state"] == "proxied"
    fetched = await world.client.get(image["url"])
    assert fetched.status_code == 200 and fetched.headers["content-type"] == "image/jpeg"
    assert fetched.headers["x-content-type-options"] == "nosniff"
    assert fetched.headers["content-security-policy"].startswith("default-src 'none'")

    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MODE", "external")
    page = (await world.client.get(f"/api/notifications/stories/{story_id}")).json()
    assert page["view"]["images"][0] == {
        "index": 0, "role": "artwork", "alt": "Cover", "state": "external", "url": URL,
    }  # fmt: skip
    assert (await world.client.get(f"/api/notifications/stories/{story_id}/images/0")).headers[
        "content-type"
    ] == "image/svg+xml"  # the proxy is not an open door while the policy is not "proxied"

    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MODE", "off")
    page = (await world.client.get(f"/api/notifications/stories/{story_id}")).json()
    assert (
        page["view"]["images"][0]["state"] == "fallback"
        and page["view"]["images"][0]["url"] is None
    )


async def test_failed_proxy_fetch_serves_fallback_artwork_not_an_error(world, network, monkeypatch):
    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MODE", "proxied")
    network.default = Reply(status=500)
    _, image = await image_state(world)
    response = await world.client.get(image["url"])
    assert response.status_code == 200 and response.headers["content-type"] == "image/svg+xml"
    assert response.headers["cache-control"] == "no-store"
    assert b"<script" not in response.content and "Image unavailable" in response.text


async def test_proxy_route_only_fetches_urls_stored_in_the_story(world, network):
    _, story_id = await world.emit()
    for index in (1, -1, 99):
        assert (
            await world.client.get(f"/api/notifications/stories/{story_id}/images/{index}")
        ).status_code == 404
    assert network.requests == []  # nothing in the request chooses what is fetched


async def test_host_media_images_resolve_only_for_the_owner(world):
    async with SessionLocal() as db:
        mine = Movie(
            user_id=world.owner,
            title="Mine",
            sort_title="mine",
            poster_url="https://img.example.com/p.jpg",
        )
        theirs = Movie(
            user_id=world.stranger,
            title="Theirs",
            sort_title="theirs",
            poster_url="https://img.example.com/q.jpg",
        )
        db.add_all([mine, theirs])
        await db.commit()
        mine_id, theirs_id = mine.id, theirs.id
    content = rich(
        images=[
            {"source": {"kind": "host_media", "media_type": "movie", "media_id": str(mine_id)}},
            {"source": {"kind": "host_media", "media_type": "movie", "media_id": str(theirs_id)}},
            {"source": {"kind": "host_media", "media_type": "game", "media_id": str(mine_id)}},
        ]
    )
    _, story_id = await world.emit(content)
    entries = (await world.client.get(f"/api/notifications/stories/{story_id}")).json()["view"][
        "images"
    ]
    assert entries[0] == {
        "index": 0, "role": "artwork", "alt": "", "state": "host_media",
        "url": f"/api/media-image/movie/{mine_id}/poster",
    }  # fmt: skip
    assert [entry["state"] for entry in entries[1:]] == ["fallback", "fallback"]
    assert str(theirs_id) not in str(entries)
