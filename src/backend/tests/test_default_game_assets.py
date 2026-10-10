import io
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src.api.routes import default_game_assets
from src.api.routes.default_game_assets import _default_cover_svg, router
from src.core.auth import get_current_user
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import get_db


def test_default_cover_svg_is_deterministic_and_title_aware() -> None:
    game_id = UUID("00000000-0000-0000-0000-000000000001")

    first = _default_cover_svg(game_id, "My Game")
    second = _default_cover_svg(game_id, "My Game")

    assert first == second
    assert 'aria-label="My Game default cover"' in first
    assert "NO COVER ART" in first


def test_default_cover_svg_escapes_title() -> None:
    game_id = UUID("00000000-0000-0000-0000-000000000002")

    svg = _default_cover_svg(game_id, "Tom & <Jerry>")

    assert "Tom &amp; &lt;Jerry&gt;" in svg
    assert "Tom & <Jerry>" not in svg


def test_preview_reuses_fallback_art_without_creating_a_game() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: object()
    with TestClient(app) as client:
        response = client.get("/api/game/preview-cover", params={"title": "Tom & <Jerry>"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert response.text == _default_cover_svg(
            UUID("00000000-0000-0000-0000-000000000001"), "Tom & <Jerry>"
        )
        assert client.get("/api/game/preview-cover", params={"title": "x" * 81}).status_code == 422


@pytest.fixture
async def game_asset_client(monkeypatch, tmp_path):
    user = User(id=uuid4(), username="cover", email="cover@example.test")
    game = Game(id=uuid4(), user_id=user.id, title="Cover Game", folder_location="Cover_Game")
    db = AsyncMock()
    db.scalar.return_value = game
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(default_game_assets, "_DATA_ROOT", tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, game, db, tmp_path


@pytest.mark.parametrize("width", [None, 400])
async def test_placeholder_revalidates_and_same_url_serves_new_art(game_asset_client, width):
    client, game, _, root = game_asset_client
    url = f"/api/game/{game.id}/assets/key_art"
    params = {} if width is None else {"w": width}
    missing = await client.get(url, params=params)
    assert missing.status_code == 200
    assert missing.headers["content-type"].startswith("image/svg+xml")
    assert missing.headers["cache-control"] == "private, no-cache"
    assert "Cover Game" in missing.text

    path = root / str(game.user_id) / "games" / game.folder_location / "key_art.png"
    path.parent.mkdir(parents=True)
    Image.new("RGB", (600, 900), (30, 90, 160)).save(path)
    uploaded = await client.get(url, params=params)
    assert uploaded.status_code == 200
    expected_type = "image/png" if width is None else "image/jpeg"
    assert uploaded.headers["content-type"] == expected_type
    assert uploaded.headers["cache-control"] == "private, max-age=3600, must-revalidate"
    with Image.open(io.BytesIO(uploaded.content)) as image:
        assert image.width == (600 if width is None else width)


async def test_missing_non_cover_and_unavailable_game_still_return_404(game_asset_client):
    client, game, db, _ = game_asset_client
    assert (await client.get(f"/api/game/{game.id}/assets/banner")).status_code == 404
    db.scalar.return_value = None
    assert (await client.get(f"/api/game/{game.id}/assets/key_art")).status_code == 404
