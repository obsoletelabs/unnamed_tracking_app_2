"""The host API the Playnite extension depends on, exercised over HTTP with a user API key.

Pins the endpoints and status codes documented in wiki/docs/integrations/playnite.md so the
game identity overhaul (#91) cannot silently break an existing Playnite installation. Every
request uses a real ``utk_`` key issued by the normal API-key route, as the extension does.
"""

import io
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy import delete

from src.api.routes import games
from src.api.routes.utils import games as game_route_helpers
from src.core.auth import hash_password
from src.database.models.user import User
from src.database.session import SessionLocal
from src.helpers import save_game_asset
from src.main import app

_PASSWORD = "Test-password!9"


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(games, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(game_route_helpers, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)
    return tmp_path


async def _key_client(stack: list) -> SimpleNamespace:
    """A user signed in once to issue a key, then an extension-style client using only it."""
    username = f"playnite_{uuid4().hex[:12]}"
    async with SessionLocal() as db:
        user = User(
            username=username,
            email=f"{username}@example.test",
            password_hash=hash_password(_PASSWORD),
        )
        db.add(user)
        await db.commit()
        user_id = user.id
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ui:
        login = await ui.post(
            "/api/auth/login", json={"username_or_email": username, "password": _PASSWORD}
        )
        assert login.status_code == 200, login.text
        key = await ui.post("/api/auth/api-keys", json={"name": "Playnite"})
        assert key.status_code == 200, key.text
    api = httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {key.json()['api_key']}"},
    )
    stack.append((api, user_id))
    return SimpleNamespace(api=api, user_id=user_id)


@pytest.fixture
async def clients():
    stack: list = []
    yield lambda: _key_client(stack)
    for api, user_id in stack:
        await api.aclose()
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


def _game(title: str, folder: str, guid: str) -> dict:
    return {"title": title, "folder_location": folder, "playnite_guid": guid}


async def test_create_is_idempotent_by_guid_across_renames_and_retries(clients):
    playnite = await clients()
    guid = str(uuid4())
    created = await playnite.api.post("/api/game/create", json=_game("Hades", "Hades", guid))
    assert created.status_code == 201, created.text
    game_id = created.json()["id"]

    # A retried create and a create sent after the title was renamed in Playnite both
    # resolve to the same game: the GUID, not the title or folder, is the identity.
    for title, folder in (("Hades", "Hades"), ("Hades (2020)", "Hades-2020")):
        again = await playnite.api.post("/api/game/create", json=_game(title, folder, guid))
        assert again.status_code == 200, again.text
        assert again.json()["id"] == game_id
        assert again.json()["title"] == "Hades"

    listed = (await playnite.api.get("/api/game/list?skip=0&limit=200")).json()
    assert [(game["id"], game["playnite_guid"]) for game in listed] == [(game_id, guid)]


async def test_folder_claimed_by_another_playnite_entry_is_a_conflict(clients):
    playnite = await clients()
    first = await playnite.api.post("/api/game/create", json=_game("Doom", "Doom", str(uuid4())))
    assert first.status_code == 201, first.text
    other = await playnite.api.post("/api/game/create", json=_game("Doom", "Doom", str(uuid4())))
    assert other.status_code == 409, other.text
    assert other.json()["detail"]["error"] == "duplicate_folder_location"


async def test_synced_state_updates_by_id_without_locking_or_dropping_the_guid(clients):
    playnite = await clients()
    guid = str(uuid4())
    game_id = (
        await playnite.api.post("/api/game/create", json=_game("Celeste", "Celeste", guid))
    ).json()["id"]
    state = {"playtime_seconds": 7_200, "status": "BEATEN", "favorite": True}
    for _ in range(2):  # a repeated sync sends the same state again
        updated = await playnite.api.patch(f"/api/game/update/{game_id}", json=state)
        assert updated.status_code == 200, updated.text
    stored = (await playnite.api.get(f"/api/game/get/{game_id}")).json()
    assert {field: stored[field] for field in state} == state
    assert stored["playnite_guid"] == guid
    assert stored["locked_fields"] == []
    assert len((await playnite.api.get("/api/game/list")).json()) == 1


async def test_list_pages_with_the_maximum_page_size(clients):
    playnite = await clients()
    for title in ("Alpha", "Bravo", "Charlie"):
        response = await playnite.api.post(
            "/api/game/create", json=_game(title, title, str(uuid4()))
        )
        assert response.status_code == 201, response.text
    first = (await playnite.api.get("/api/game/list?skip=0&limit=2")).json()
    rest = (await playnite.api.get("/api/game/list?skip=2&limit=2")).json()
    assert [game["title"] for game in first + rest] == ["Alpha", "Bravo", "Charlie"]
    assert (await playnite.api.get("/api/game/list?limit=200")).status_code == 200
    # Bulk workflows may now request 1,000 rows; the established 200-row client
    # remains valid and the public endpoint still enforces a finite hard cap.
    assert (await playnite.api.get("/api/game/list?limit=201")).status_code == 200
    assert (await playnite.api.get("/api/game/list?limit=1000")).status_code == 200
    assert (await playnite.api.get("/api/game/list?limit=1001")).status_code == 422
    assert (await playnite.api.get("/api/game/list?limit=0")).status_code == 422


async def test_artwork_over_one_megabyte_uploads_with_the_api_key(clients, storage):
    playnite = await clients()
    game_id = (
        await playnite.api.post("/api/game/create", json=_game("Tunic", "Tunic", str(uuid4())))
    ).json()["id"]
    buffer = io.BytesIO()
    Image.frombytes("RGB", (800, 800), os.urandom(800 * 800 * 3)).save(buffer, "PNG")
    assert buffer.tell() > 1024 * 1024  # above the anonymous request-body limit
    uploaded = await playnite.api.post(
        f"/api/game/{game_id}/assets/key_art",
        files={"file": ("cover.png", buffer.getvalue(), "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["status"] == "saved"
    assert (storage / str(playnite.user_id) / "games" / "Tunic" / "key_art.png").is_file()


async def test_a_key_only_reaches_its_own_users_library(clients):
    owner, stranger = await clients(), await clients()
    game_id = (
        await owner.api.post("/api/game/create", json=_game("Hollow", "Hollow", str(uuid4())))
    ).json()["id"]
    assert (await stranger.api.get(f"/api/game/get/{game_id}")).status_code == 404
    patched = await stranger.api.patch(f"/api/game/update/{game_id}", json={"favorite": True})
    assert patched.status_code == 404
    assert (await stranger.api.get("/api/game/list")).json() == []
    assert (await owner.api.get(f"/api/game/get/{game_id}")).json()["favorite"] is False
