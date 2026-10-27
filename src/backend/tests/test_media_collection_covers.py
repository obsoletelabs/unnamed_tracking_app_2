"""Media collections use ordered cached artwork without per-title metadata queries."""

import io
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import delete, event

from src.api.routes import media_collection_covers, media_lists
from src.core.auth import get_current_user
from src.database.models.media_extras import MediaList, MediaListItem, MediaType
from src.database.models.movies import Movie
from src.database.models.user import User
from src.database.session import SessionLocal, engine, get_db
from src.helpers import media_collection_artwork


@pytest.fixture
async def collections(monkeypatch, tmp_path):
    user = User(
        id=uuid4(),
        username=f"media-{uuid4().hex[:10]}",
        email=f"{uuid4().hex}@example.test",
        password_hash="unused",
    )
    async with SessionLocal() as db:
        db.add(user)
        await db.commit()
    app = FastAPI()
    app.include_router(media_lists.router)
    app.include_router(media_collection_covers.router)
    who = {"user": user}
    app.dependency_overrides[get_current_user] = lambda: who["user"]

    async def session():
        async with SessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = session
    monkeypatch.setattr(media_collection_artwork, "_DATA_ROOT", tmp_path)

    def no_network(*_args):
        raise AssertionError("The test's cached posters must not be downloaded")

    monkeypatch.setattr(media_collection_covers, "fetch_and_store", no_network)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, user, who, tmp_path
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()


async def seed(user, count=5):
    movies = [
        Movie(
            id=uuid4(),
            user_id=user.id,
            title=f"Movie {index}",
            sort_title=f"movie {index}",
            poster_url=f"https://example.test/{index}.jpg",
            favorite=index % 2 == 0,
            genres=["Drama"],
            rating_overall=8,
        )
        for index in range(count)
    ]
    lst = MediaList(id=uuid4(), user_id=user.id, name="My media")
    async with SessionLocal() as db:
        db.add_all([*movies, lst])
        await db.flush()
        members = [
            MediaListItem(
                id=uuid4(),
                list_id=lst.id,
                media_type=MediaType.MOVIE,
                media_id=movie.id,
                position=index,
            )
            for index, movie in enumerate(movies)
        ]
        db.add_all(members)
        await db.commit()
    return lst, movies, members


async def test_cover_honors_order_pick_cache_and_account(collections):
    client, user, who, _ = collections
    lst, movies, members = await seed(user)
    colors = [(220, 20, 25), (25, 190, 35), (30, 40, 220), (220, 190, 20), (190, 30, 190)]
    for movie, color in zip(movies, colors, strict=True):
        path = media_collection_artwork.poster_path(
            user.id, {"media_id": movie.id, "media_type": "movie", "poster_url": movie.poster_url}
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (80, 120), color).save(path, format="JPEG")
    url = f"/api/lists/{lst.id}/cover?owner={user.id}"
    first = await client.get(url)
    assert first.status_code == 200
    assert first.headers["cache-control"].startswith("private, max-age=60")
    with Image.open(io.BytesIO(first.content)) as image:
        assert image.size == (400, 600)
        assert image.getpixel((100, 150))[0] > 190
    cached = await client.get(url, headers={"If-None-Match": first.headers["etag"]})
    assert cached.status_code == 304
    ordered = list(reversed(members))
    assert (
        await client.put(
            f"/api/lists/{lst.id}/order", json={"item_ids": [str(item.id) for item in ordered]}
        )
    ).status_code == 204
    reordered = await client.get(url)
    assert reordered.headers["etag"] != first.headers["etag"]
    with Image.open(io.BytesIO(reordered.content)) as image:
        assert image.getpixel((100, 150))[2] > 150
    await client.patch(f"/api/lists/{lst.id}", json={"cover_media_id": str(movies[1].id)})
    picked = await client.get(url)
    with Image.open(io.BytesIO(picked.content)) as image:
        assert image.getpixel((100, 150))[1] > 160
    who["user"] = User(id=uuid4())
    assert (await client.get(url)).status_code == 404
    assert (await client.delete(f"/api/lists/{lst.id}/cover")).status_code == 404


async def test_empty_custom_cover_upload_remove_rename_and_delete(collections):
    client, user, _, root = collections
    created = (await client.post("/api/lists", json={"name": "Empty"})).json()
    url = created["cover_url"]
    assert "owner=" + str(user.id) in url
    empty = await client.get(url)
    assert empty.status_code == 200
    source = io.BytesIO()
    Image.new("RGB", (80, 120), (210, 30, 40)).save(source, format="PNG")
    endpoint = f"/api/lists/{created['id']}/cover"
    uploaded = await client.put(
        endpoint, files={"file": ("cover.png", source.getvalue(), "image/png")}
    )
    assert uploaded.status_code == 200
    detail = (await client.get(f"/api/lists/{created['id']}")).json()
    assert detail["has_custom_cover"] and detail["cover_url"] != url
    assert (
        await client.put(endpoint, files={"file": ("bad.png", b"invalid", "image/png")})
    ).status_code == 400
    custom = await client.get(detail["cover_url"])
    with Image.open(io.BytesIO(custom.content)) as image:
        assert image.getpixel((200, 300))[0] > 180
    await client.patch(f"/api/lists/{created['id']}", json={"name": "Renamed"})
    assert (await client.get(endpoint)).content == custom.content
    assert (await client.delete(endpoint)).status_code == 204
    assert not (await client.get(f"/api/lists/{created['id']}")).json()["has_custom_cover"]
    await client.put(endpoint, files={"file": ("cover.png", source.getvalue(), "image/png")})
    assert (await client.delete(f"/api/lists/{created['id']}")).status_code == 204
    assert not media_collection_artwork.custom_cover_path(user.id, created["id"]).exists()
    assert (await client.get(endpoint)).status_code == 404


async def test_summaries_batch_queries_and_retain_counts_and_legacy_previews(collections):
    client, user, _, _ = collections
    lst, _, _ = await seed(user, 30)
    await client.post(
        "/api/lists", json={"name": "Drama", "smart_rule": {"genre": "drama", "min_score": 7}}
    )
    await client.get("/api/lists")  # Create Favorites and warm preferences first.
    statements = []

    def observed(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", observed)
    try:
        response = await client.get("/api/lists?include_previews=false")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", observed)
    assert response.status_code == 200
    assert len(statements) <= 10
    values = {value["name"]: value for value in response.json()}
    assert values["My media"]["item_count"] == 30
    assert values["Drama"]["item_count"] == 30
    assert values["Favorites"]["item_count"] == 15
    assert all(value["preview_posters"] == [] for value in values.values())
    assert all("/cover?owner=" in value["cover_url"] for value in values.values())
    legacy = (await client.get("/api/lists")).json()
    assert (
        len(next(value for value in legacy if value["id"] == str(lst.id))["preview_posters"]) == 4
    )
