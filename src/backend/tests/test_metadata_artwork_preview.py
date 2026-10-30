"""Selected previews retain ownership, provider authorization and the pinned image boundary."""

import io
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from test_core_metadata import native_boundary  # noqa: F401
from test_metadata_provider_configuration import provider_boundary  # noqa: F401
from test_plugin_authorization_http import boundary  # noqa: F401

from src.api.routes import metadata as routes
from src.core.auth import session_cookie_name
from src.features.metadata import artwork
from src.features.metadata.core import discover_core_providers
from src.features.metadata.handler import SearchSession
from src.helpers.remote_images import RemoteImageError
from src.plugin_api.metadata_contracts import MetadataProviderRequest


@pytest.mark.parametrize(
    "case", ["valid", "foreign", "unselected", "invented", "revoked", "invalid"]
)
async def test_owned_selected_artwork_only(native_boundary, monkeypatch, case):  # noqa: F811
    item = native_boundary
    provider = next(
        p
        for p in await discover_core_providers(item.database, item.users[0].id)
        if p.id == "core.steam"
    )
    identity = str(uuid4())
    url = "https://public.example/cover.png"
    session = SearchSession(
        MetadataProviderRequest(request_id=uuid4(), user_id=item.users[0].id, query="Portal"),
        (provider,),
        selected=identity,
        candidates={identity: {"assets": [{"kind": "key_art", "url": url}]}},
        asset_providers={("key_art", url): {provider.id}},
    )
    routes.handler.sessions[session.id] = session
    calls = []

    def download(value, *, max_bytes, cache_kind):
        assert cache_kind == "artwork_preview"
        calls.append((value, max_bytes))
        if case == "invalid":
            raise RemoteImageError("bad image")
        output = io.BytesIO()
        Image.new("RGB", (900, 1200)).save(output, format="PNG")
        return output.getvalue()

    monkeypatch.setattr(artwork, "download_image", download)
    if case == "unselected":
        session.selected = None
    if case == "revoked":

        async def denied(_operation):
            return False

        monkeypatch.setattr(type(provider), "authorized", lambda *_args: denied("media"))
    try:
        user = item.users[1] if case == "foreign" else item.users[0]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=item.app),
            base_url="http://test",
            cookies={session_cookie_name("test"): item.tokens[user.id]},
        ) as client:
            response = await client.post(
                f"/api/metadata/sessions/{session.id}/artwork",
                json={
                    "candidate_id": identity,
                    "url": url + "?invented" if case == "invented" else url,
                },
            )
        expected = {
            "valid": 200,
            "foreign": 404,
            "unselected": 404,
            "invented": 404,
            "revoked": 403,
            "invalid": 400,
        }
        assert response.status_code == expected[case], response.text
        if case == "valid":
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            with Image.open(io.BytesIO(response.content)) as image:
                assert image.format == "PNG" and max(image.size) == 800
        assert len(calls) == (1 if case in {"valid", "invalid"} else 0)
    finally:
        routes.handler.sessions.pop(session.id, None)
