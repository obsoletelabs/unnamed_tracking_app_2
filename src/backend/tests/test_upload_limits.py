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
def boundary():
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


@pytest.mark.parametrize("name", UPLOAD_LIMIT_FIELDS)
@pytest.mark.asyncio
async def test_precedence_environment_then_saved_override_then_default(boundary, monkeypatch, name):
    """#253: an explicit environment value wins and is locked; otherwise the saved value; otherwise the default."""
    from src.core.app_integrations import get_upload_limit_sources

    app, row, db, _ = boundary
    env_name = name.upper()
    default = getattr(settings, env_name)
    monkeypatch.delenv(env_name, raising=False)

    assert (await get_upload_limit_sources(db))[name]["source"] == "default"
    setattr(row, name, 7)
    saved = (await get_upload_limit_sources(db))[name]
    assert (saved["value"], saved["source"], saved["locked"]) == (7, "override", False)

    monkeypatch.setenv(env_name, "42")
    explicit = (await get_upload_limit_sources(db))[name]
    assert (explicit["value"], explicit["source"], explicit["locked"]) == (42, "environment", True)
    assert explicit["environment_variable"] == env_name and explicit["stored_override"] == 7

    for bad in (
        "abc",
        "0",
        "-3",
        "1.5",
        "99999999999",
    ):  # unusable values are ignored, never half-applied
        monkeypatch.setenv(env_name, bad)
        found = (await get_upload_limit_sources(db))[name]
        assert (found["value"], found["source"]) == (7, "override"), bad
        assert found["environment_invalid"] is True and found["locked"] is False
    monkeypatch.delenv(env_name)
    setattr(row, name, None)
    assert (await get_upload_limit_sources(db))[name]["value"] == default


@pytest.mark.asyncio
async def test_environment_locked_caps_refuse_edits_without_changing_anything(
    boundary, monkeypatch
):
    app, row, db, _ = boundary
    monkeypatch.setenv("MAX_DOC_SIZE_MB", "64")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listing = (await client.get("/api/settings/upload-limits")).json()
        assert listing["max_doc_size_mb"] == 64  # the environment decides what uploads are held to
        details = (await client.get("/api/settings/upload-limit-sources")).json()
        assert (
            details["max_doc_size_mb"]["locked"] is True
            and details["max_upload_size_mb"]["locked"] is False
        )
        refused = await client.put(
            "/api/settings/upload-limit", json={"max_doc_size_mb": 128, "max_clip_size_mb": 9}
        )
        assert refused.status_code == 409
        assert "MAX_DOC_SIZE_MB" in refused.json()["detail"]
        assert (
            row.max_doc_size_mb is None and row.max_clip_size_mb is None
        )  # the other field was not saved
        db.commit.assert_not_awaited()
        # editable fields still save, and clearing a locked field's stored value is harmless
        ok = await client.put(
            "/api/settings/upload-limit", json={"max_clip_size_mb": 9, "max_doc_size_mb": None}
        )
        assert (
            ok.status_code == 200
            and ok.json()["max_clip_size_mb"] == 9
            and ok.json()["max_doc_size_mb"] == 64
        )
