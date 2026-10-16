"""Scan settings save metadata provider IDs that the account can actually discover."""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.models.user_scan_settings import UserScanSettings
from src.database.session import SessionLocal
from src.features.metadata.providers import discover_providers
from src.main import app


@pytest.fixture
async def account():
    async with SessionLocal() as db:
        user = User(
            username=f"scan_{uuid4().hex[:10]}",
            email=f"{uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.commit()
        identity = user.id

    async def current_actor():
        async with SessionLocal() as db:
            return await db.get(User, identity)

    app.dependency_overrides[get_current_user] = current_actor
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, identity
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == identity))
            await db.commit()


async def test_saved_provider_ids_decide_discovery_priority(account):
    client, identity = account
    order = ["core.steam", "core.tvmaze", "core.igdb"]
    saved = await client.put("/api/settings/scan", json={"provider_order": order})
    assert saved.status_code == 200
    assert saved.json()["provider_order"] == order
    async with SessionLocal() as db:
        discovered = await discover_providers(db, identity)
    priorities = {provider.id: provider.priority for provider in discovered}
    assert priorities["core.steam"] < priorities["core.tvmaze"] < priorities["core.igdb"]
    assert priorities["core.omdb"] == 100


@pytest.mark.parametrize(
    "order",
    [
        ["core.steam", "other.plugin.provider"],
        ["Steam"],
        ["core.steam", "core.steam"],
    ],
)
async def test_unknown_legacy_or_duplicate_provider_ids_are_refused(account, order):
    client, identity = account
    refused = await client.put("/api/settings/scan", json={"provider_order": order})
    assert refused.status_code == 422
    async with SessionLocal() as db:
        saved = await db.scalar(
            select(UserScanSettings.provider_order).where(UserScanSettings.user_id == identity)
        )
    assert saved is None


async def test_legacy_display_name_order_still_applies(account):
    _, identity = account
    async with SessionLocal() as db:
        db.add(UserScanSettings(user_id=identity, provider_order=["Steam", "IGDB"]))
        await db.commit()
        discovered = await discover_providers(db, identity)
        priorities = {provider.id: provider.priority for provider in discovered}
    assert priorities["core.steam"] == 0
    assert priorities["core.igdb"] == 1
