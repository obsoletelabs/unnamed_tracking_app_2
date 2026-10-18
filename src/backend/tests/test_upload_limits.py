"""Administrator limit changes preserve other caps and their environment fallbacks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes.settings import router
from src.core.app_integrations import UPLOAD_LIMIT_FIELDS
from src.core.auth import get_current_user
from src.core.config import settings
from src.database.session import get_db


@pytest.fixture
def boundary(monkeypatch):
    # Developer .env files commonly set these; each test chooses its own environment.
    for name in UPLOAD_LIMIT_FIELDS:
        monkeypatch.delenv(name.upper(), raising=False)
    row = SimpleNamespace(**dict.fromkeys(UPLOAD_LIMIT_FIELDS), tmdb_api_key="unchanged")
    db = SimpleNamespace(scalar=AsyncMock(return_value=row), commit=AsyncMock())
    user = SimpleNamespace(is_admin=True)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return app, row, db, user


@pytest.mark.asyncio
async def test_environment_defaults_and_partial_update_reset(boundary):
    app, row, db, _ = boundary
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        defaults = (await client.get("/api/settings/upload-limits")).json()
        assert defaults == {name: getattr(settings, name.upper()) for name in UPLOAD_LIMIT_FIELDS}
        response = await client.put(
            "/api/settings/upload-limit", json={"max_clip_size_mb": 3, "max_world_save_size_mb": 7}
        )
        assert response.status_code == 200
        assert response.json() == {**defaults, "max_clip_size_mb": 3, "max_world_save_size_mb": 7}
        response = await client.put("/api/settings/upload-limit", json={"max_clip_size_mb": None})
        assert response.json() == {**defaults, "max_world_save_size_mb": 7}
        assert row.max_upload_size_mb is None
        assert row.max_save_archive_size_mb is None
        assert row.tmdb_api_key == "unchanged"
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_documents_have_their_own_cap_independent_of_the_image_cap(boundary):
    app, row, _, _ = boundary
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        defaults = (await client.get("/api/settings/upload-limits")).json()
        assert defaults["max_doc_size_mb"] == settings.MAX_DOC_SIZE_MB
        assert settings.MAX_DOC_SIZE_MB > settings.MAX_UPLOAD_SIZE_MB
        response = await client.put("/api/settings/upload-limit", json={"max_doc_size_mb": 64})
        assert response.json() == {**defaults, "max_doc_size_mb": 64}
        response = await client.put("/api/settings/upload-limit", json={"max_upload_size_mb": 2})
        assert response.json() == {**defaults, "max_upload_size_mb": 2, "max_doc_size_mb": 64}
    assert row.max_doc_size_mb == 64 and row.max_upload_size_mb == 2


@pytest.mark.asyncio
async def test_member_can_read_but_cannot_change_any_limit(boundary):
    app, _, db, user = boundary
    user.is_admin = False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/settings/upload-limits")).status_code == 200
        for name in UPLOAD_LIMIT_FIELDS:
            assert (
                await client.put("/api/settings/upload-limit", json={name: 1})
            ).status_code == 403
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("value", [0, -1, 1.5, "2", True, 2147483648])
@pytest.mark.asyncio
async def test_invalid_caps_are_rejected_without_partial_writes(boundary, value):
    app, row, db, _ = boundary
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for name in UPLOAD_LIMIT_FIELDS:
            response = await client.put("/api/settings/upload-limit", json={name: value})
            assert response.status_code == 422
            assert getattr(row, name) is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_cap_is_not_silently_ignored(boundary):
    app, _, db, _ = boundary
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/api/settings/upload-limit", json={"max_plugin_size_mb": 10})
        assert response.status_code == 422
    db.commit.assert_not_awaited()


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("name", UPLOAD_LIMIT_FIELDS)
@pytest.mark.asyncio
async def test_precedence_default_override_then_environment(boundary, monkeypatch, name):
    app, row, _, _ = boundary
    env_name = name.upper()
    async with _client(app) as client:
        details = (await client.get("/api/settings/upload-limits/details")).json()[name]
        assert details["source"] == "default" and not details["locked"]
        assert details["value"] == getattr(settings, env_name)

        assert (await client.put("/api/settings/upload-limit", json={name: 9})).status_code == 200
        details = (await client.get("/api/settings/upload-limits/details")).json()[name]
        assert (details["value"], details["source"]) == (9, "override")

        monkeypatch.setenv(env_name, "33")
        details = (await client.get("/api/settings/upload-limits/details")).json()[name]
        assert (details["value"], details["source"], details["locked"]) == (33, "environment", True)
        assert (await client.get("/api/settings/upload-limits")).json()[name] == 33
    assert getattr(row, name) == 9  # the stored override is kept for when the environment goes


@pytest.mark.parametrize("raw", ["abc", "0", "-4", "1.5", "99999999999"])
@pytest.mark.asyncio
async def test_invalid_environment_value_is_ignored_and_reported(boundary, monkeypatch, raw):
    app, _, _, _ = boundary
    monkeypatch.setenv("MAX_CLIP_SIZE_MB", raw)
    async with _client(app) as client:
        details = (await client.get("/api/settings/upload-limits/details")).json()
        clip = details["max_clip_size_mb"]
        assert clip["source"] == "default" and not clip["locked"]
        assert clip["environment_invalid"] is True
        assert clip["value"] == settings.MAX_CLIP_SIZE_MB


@pytest.mark.asyncio
async def test_saving_an_environment_locked_cap_is_rejected_without_changing_others(
    boundary, monkeypatch
):
    app, row, db, _ = boundary
    monkeypatch.setenv("MAX_DOC_SIZE_MB", "77")
    async with _client(app) as client:
        response = await client.put(
            "/api/settings/upload-limit", json={"max_doc_size_mb": 5, "max_clip_size_mb": 6}
        )
        assert response.status_code == 409
        assert "MAX_DOC_SIZE_MB" in response.json()["detail"]
        # Clearing a locked cap is a change too.
        response = await client.put("/api/settings/upload-limit", json={"max_doc_size_mb": None})
        assert response.status_code == 409
        # Unlocked caps still save while another cap is locked.
        response = await client.put("/api/settings/upload-limit", json={"max_clip_size_mb": 6})
        assert response.status_code == 200
        assert response.json()["max_doc_size_mb"] == 77 and response.json()["max_clip_size_mb"] == 6
    assert row.max_doc_size_mb is None and row.max_clip_size_mb == 6
    assert db.commit.await_count == 1
