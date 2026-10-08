"""Profile uploads decode real HEIC data and still reject invalid images."""

import io
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from pillow_heif import from_pillow

from src.api.routes import users
from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db


@pytest.fixture
async def profile_client(monkeypatch, tmp_path):
    user = User(id=uuid.uuid4(), username="profile", email="profile@example.test")
    db = AsyncMock()
    db.get.return_value = user
    app = FastAPI()
    app.include_router(users.router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(users, "_USER_DATA_ROOT", tmp_path)
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
