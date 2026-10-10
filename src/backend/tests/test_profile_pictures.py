"""Profile uploads decode real HEIC data and still reject invalid images."""

import io
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from pillow_heif import from_pillow

from src.api.routes import auth, users
from src.core.auth import get_current_actor, get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features import profile_pictures


@pytest.fixture
async def profile_client(monkeypatch, tmp_path):
    user = User(id=uuid.uuid4(), username="profile", email="profile@example.test", is_admin=False)
    db = AsyncMock()
    db.get.return_value = user
    app = FastAPI()
    app.include_router(users.router)
    app.include_router(auth.router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_actor] = lambda: SimpleNamespace(credential_kind="api_key")
    monkeypatch.setattr(profile_pictures, "_USER_DATA_ROOT", tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, user.id, tmp_path


@pytest.mark.parametrize("format_name", ["HEIC", "PNG", "JPEG"])
async def test_profile_upload_is_stored_and_served_as_png(profile_client, format_name):
    client, user_id, root = profile_client
    buffer = io.BytesIO()
    image = Image.new("RGB", (32, 24), (20, 80, 140))
    if format_name == "HEIC":
        from_pillow(image).save(buffer)
    else:
        image.save(buffer, format_name)
    response = await client.put(
        f"/api/user/{user_id}/profile-picture",
        files={
            "file": (
                f"portrait.{format_name.lower()}",
                buffer.getvalue(),
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 200, response.text
    saved = Image.open(root / str(user_id) / "profile.png")
    assert saved.format == "PNG" and saved.size == (32, 24)
    fetched = await client.get(f"/api/user/{user_id}/profile-picture")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    version = response.json()["profile_picture_version"]
    assert (
        version and (await client.get("/api/auth/me")).json()["profile_picture_version"] == version
    )
    assert fetched.headers["cache-control"] == "private, no-store"
    cached = await client.get(f"/api/user/{user_id}/profile-picture", params={"v": version})
    assert cached.headers["cache-control"] == "private, max-age=86400, immutable"


@pytest.mark.parametrize(
    "data,status",
    [(b"", 400), (b"invalid image", 400), (b"x" * (10 * 1024 * 1024 + 1), 413)],
    ids=["empty", "invalid", "oversized"],
)
async def test_invalid_upload_does_not_replace_the_picture(profile_client, data, status):
    client, user_id, root = profile_client
    directory = root / str(user_id)
    directory.mkdir()
    target = directory / "profile.png"
    target.write_bytes(b"original")
    response = await client.put(
        f"/api/user/{user_id}/profile-picture",
        files={"file": ("portrait.heic", data, "image/heic")},
    )
    assert response.status_code == status
    assert target.read_bytes() == b"original"


async def test_upload_cannot_change_another_users_picture(profile_client):
    client, _, _ = profile_client
    response = await client.put(
        f"/api/user/{uuid.uuid4()}/profile-picture",
        files={"file": ("portrait.heic", b"invalid", "image/heic")},
    )
    assert response.status_code == 403


async def test_missing_picture_is_explicit_and_existing_profiles_need_no_migration(profile_client):
    client, user_id, root = profile_client
    assert (await client.get("/api/auth/me")).json()["profile_picture_version"] is None
    assert (await client.get(f"/api/user/{user_id}/profile-picture")).status_code == 404
    directory = root / str(user_id)
    directory.mkdir()
    Image.new("RGB", (8, 8)).save(directory / "profile.png")
    version = (await client.get("/api/auth/me")).json()["profile_picture_version"]
    assert version
    updated = await client.patch("/api/auth/me", json={"username": "profile-renamed"})
    assert updated.status_code == 200
    assert updated.json()["profile_picture_version"] == version


async def test_replacement_invalidates_version_and_stale_urls_are_not_immutable(profile_client):
    client, user_id, _ = profile_client
    versions = []
    for color in ("red", "blue"):
        image = io.BytesIO()
        Image.new("RGB", (8, 8), color).save(image, "PNG")
        response = await client.put(
            f"/api/user/{user_id}/profile-picture",
            files={"file": ("avatar.png", image.getvalue(), "image/png")},
        )
        assert response.status_code == 200
        versions.append(response.json()["profile_picture_version"])
    assert versions[0] != versions[1]
    stale = await client.get(f"/api/user/{user_id}/profile-picture", params={"v": versions[0]})
    assert stale.headers["cache-control"] == "private, no-store"
    assert Image.open(io.BytesIO(stale.content)).getpixel((0, 0)) == (0, 0, 255, 255)
    assert (await client.get("/api/auth/me")).json()["profile_picture_version"] == versions[1]


async def test_picture_cache_does_not_bypass_authorization(profile_client):
    client, _, root = profile_client
    other = uuid.uuid4()
    directory = root / str(other)
    directory.mkdir()
    Image.new("RGB", (8, 8)).save(directory / "profile.png")
    version = await profile_pictures.profile_picture_version(other)
    response = await client.get(f"/api/user/{other}/profile-picture", params={"v": version})
    assert response.status_code == 403


async def test_unavailable_picture_storage_does_not_break_current_user(profile_client, monkeypatch):
    client, _, _ = profile_client

    def unavailable(_path):
        raise PermissionError("Unavailable optional avatar")

    monkeypatch.setattr(profile_pictures.Path, "stat", unavailable)
    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["profile_picture_version"] is None
