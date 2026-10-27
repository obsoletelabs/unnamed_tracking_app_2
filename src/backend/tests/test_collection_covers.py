"""Collection covers use one authenticated image and honor custom uploads."""

import io
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from src.api.routes import collection_covers
from src.core.auth import get_current_user
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import get_db


@pytest.fixture
async def cover_client(monkeypatch, tmp_path):
    user = User(id=uuid4(), username="covers", email="covers@example.test")
    db = AsyncMock()
    db.scalars.return_value = MagicMock()
    app = FastAPI()
    app.include_router(collection_covers.router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(collection_covers, "_DATA_ROOT", tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, user, db, tmp_path


async def test_empty_collection_is_one_placeholder_and_upload_can_be_removed(cover_client):
    client, _, _, _ = cover_client
    url = "/api/game/collection-cover"
    generated = await client.get(url, params={"name": "Favorites"})
    assert generated.status_code == 200
    assert generated.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(generated.content)) as image:
        assert image.size == (400, 600)

    source = io.BytesIO()
    Image.new("RGB", (80, 120), (220, 35, 45)).save(source, format="PNG")
    uploaded = await client.put(
        url,
        data={"name": "Favorites"},
        files={"file": ("cover.png", source.getvalue(), "image/png")},
    )
    assert uploaded.status_code == 200
    custom = await client.get(url, params={"name": "Favorites"})
    assert custom.status_code == 200
    with Image.open(io.BytesIO(custom.content)) as image:
        assert image.getpixel((200, 300))[0] > 190
    cached = await client.get(
        url,
        params={"name": "Favorites"},
        headers={"If-None-Match": custom.headers["etag"]},
    )
    assert cached.status_code == 304
    moved = await client.post(
        "/api/game/collection-cover/rename",
        json={"old_name": "Favorites", "new_name": "Renamed"},
    )
    assert moved.status_code == 204
    renamed = await client.get(url, params={"name": "Renamed"})
    assert renamed.content == custom.content
    assert (await client.delete(url, params={"name": "Renamed"})).status_code == 204
    restored = await client.get(url, params={"name": "Favorites"})
    assert restored.content == generated.content


async def test_ordered_four_game_collage_and_owner_check(cover_client):
    client, user, db, root = cover_client
    games = [
        Game(id=uuid4(), user_id=user.id, title=f"Game {index}", folder_location=f"game-{index}")
        for index in range(4)
    ]
    colors = [(210, 25, 30), (25, 180, 40), (30, 55, 210), (210, 180, 20)]
    for game, color in zip(games, colors, strict=True):
        path = root / str(user.id) / "games" / game.folder_location / "key_art.png"
        path.parent.mkdir(parents=True)
        Image.new("RGB", (120, 180), color).save(path)
    db.scalars.return_value.all.return_value = games
    params = [("name", "My Order"), *[("ids", str(game.id)) for game in games]]
    response = await client.get("/api/game/collection-cover", params=params)
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (400, 600)
        for point, color in zip(
            [(100, 150), (300, 150), (100, 450), (300, 450)], colors, strict=True
        ):
            actual = image.getpixel(point)
            assert all(abs(a - b) < 35 for a, b in zip(actual, color, strict=True))
    db.scalars.return_value.all.return_value = games[:3]
    forbidden = await client.get("/api/game/collection-cover", params=params)
    assert forbidden.status_code == 404


async def test_invalid_upload_does_not_replace_cover(cover_client):
    client, _, _, _ = cover_client
    response = await client.put(
        "/api/game/collection-cover",
        data={"name": "Bad"},
        files={"file": ("invalid.png", b"not an image", "image/png")},
    )
    assert response.status_code == 400


async def test_no_real_art_uses_one_full_tile_placeholder(cover_client):
    client, user, db, _ = cover_client
    games = [
        Game(
            id=uuid4(),
            user_id=user.id,
            title=f"Missing {index}",
            folder_location=f"missing-{index}",
        )
        for index in range(4)
    ]
    db.scalars.return_value.all.return_value = games
    params = [("name", "Missing Art"), *[("ids", str(game.id)) for game in games]]
    with_games = await client.get("/api/game/collection-cover", params=params)
    empty = await client.get("/api/game/collection-cover", params={"name": "Missing Art"})
    assert with_games.status_code == 200
    assert with_games.content == empty.content


async def test_cover_cache_is_partitioned_by_account(cover_client):
    client, user, _, _ = cover_client
    response = await client.get(
        "/api/game/collection-cover", params={"name": "Favorites", "owner": str(uuid4())}
    )
    assert response.status_code == 403
    own = await client.get(
        "/api/game/collection-cover", params={"name": "Favorites", "owner": str(user.id)}
    )
    assert own.status_code == 200
    assert own.headers["cache-control"].startswith("private, max-age=60")
