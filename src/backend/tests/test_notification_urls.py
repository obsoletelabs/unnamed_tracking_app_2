"""Owner-specific origins, override priority and isolation from authentication callbacks."""

from functools import partial
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from src.api.routes.auth import router as auth_router
from src.api.routes.notification_providers import router
from src.core.app_integrations import get_or_create_app_integration_settings
from src.core.auth import AuthenticatedActor, get_current_actor, get_current_user
from src.core.preferences import save_preferences
from src.database.models.notification_destination import NotificationDestination
from src.database.models.user import User
from src.database.session import SessionLocal, get_db
from src.features.notification_urls import notification_url


@pytest.fixture
async def accounts(monkeypatch):
    monkeypatch.delenv("PUBLIC_APP_URL", raising=False)
    identities = [uuid4(), uuid4()]
    async with SessionLocal() as db:
        integration = await get_or_create_app_integration_settings(db)
        previous_url = integration.public_app_url
        integration.public_app_url = None
        users = [
            User(
                id=identity,
                username=identity.hex,
                email=f"{identity}@example.test",
                password_hash="unused",
            )
            for identity in identities
        ]
        db.add_all(users)
        await db.commit()
        try:
            yield db, users
        finally:
            await db.rollback()
            await db.refresh(integration)
            integration.public_app_url = previous_url
            await db.execute(delete(User).where(User.id.in_(identities)))
            await db.commit()


async def test_browser_origins_are_per_user_and_api_keys_cannot_change_them(accounts):
    db, users = accounts
    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: db
    for user, origin in zip(
        users, ["http://192.168.1.7:8080", "https://second.example.test"], strict=True
    ):
        app.dependency_overrides[get_current_user] = partial(lambda selected: selected, user)
        app.dependency_overrides[get_current_actor] = partial(
            AuthenticatedActor, user.id, "session"
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url=origin) as client:
            response = await client.get("/api/auth/me")
            assert response.status_code == 200 and user.last_app_url == origin
            response = await client.get(
                "/api/auth/me",
                headers={"sec-fetch-site": "cross-site", "host": "poison.example.test"},
            )
            assert response.status_code == 200 and user.last_app_url == origin
    app.dependency_overrides[get_current_actor] = lambda: AuthenticatedActor(
        users[-1].id, "api_key"
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://automation.example.test"
    ) as client:
        assert (await client.get("/api/auth/me")).status_code == 200
    assert users[0].last_app_url == "http://192.168.1.7:8080"
    assert users[1].last_app_url == "https://second.example.test"


async def test_notification_url_priority_is_destination_user_deployment_last_used(
    accounts, monkeypatch
):
    db, users = accounts
    user = users[0]
    user.last_app_url = "http://192.168.1.7:8080"
    await db.commit()
    assert await notification_url(db, user.id) == user.last_app_url
    assert await notification_url(db, users[1].id) == ""
    monkeypatch.setenv("PUBLIC_APP_URL", "https://shared.example.test")
    assert await notification_url(db, user.id) == "https://shared.example.test"
    await save_preferences(db, user.id, {"notification_url": "https://personal.example.test/"})
    assert await notification_url(db, user.id) == "https://personal.example.test"
    destination = NotificationDestination(
        user_id=user.id,
        notification_url="https://email.example.test",
        revision=1,
        verified_revision=1,
    )
    assert await notification_url(db, user.id, destination) == "https://email.example.test"
    assert destination.revision == destination.verified_revision == 1
    destination.notification_url = None
    assert await notification_url(db, user.id, destination) == "https://personal.example.test"
    with pytest.raises(ValueError, match="another user"):
        await notification_url(db, users[1].id, destination)


async def test_destination_url_endpoint_enforces_owner_and_preserves_proof(accounts):
    db, users = accounts
    first, second = users
    destination = NotificationDestination(
        user_id=first.id,
        provider_id="core.smtp",
        kind="email",
        channel_context="external",
        endpoint_key="opaque",
        privacy=1,
        revision=3,
        verified_revision=3,
    )
    db.add(destination)
    await db.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: second
    path = f"/api/settings/notification-providers/destinations/{destination.id}/url"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.patch(path, json={"notification_url": "https://mail.example.test"})
        ).status_code == 404
        app.dependency_overrides[get_current_user] = lambda: first
        for invalid in [
            "javascript:alert(1)",
            "https://user:secret@example.test",
            "https://example.test/?token=secret",
        ]:
            assert (await client.patch(path, json={"notification_url": invalid})).status_code == 422
        assert (
            await client.patch(path, json={"notification_url": "https://mail.example.test/"})
        ).status_code == 200
        assert destination.notification_url == "https://mail.example.test"
        assert destination.revision == destination.verified_revision == 3
        assert (await client.patch(path, json={"notification_url": ""})).status_code == 200
        assert destination.notification_url is None


@pytest.mark.parametrize(
    "invalid", [None, 17, {}, "javascript:alert(1)", "https://name:password@example.test"]
)
async def test_personal_notification_url_validation(accounts, invalid):
    db, users = accounts
    with pytest.raises(ValueError):
        await save_preferences(db, users[0].id, {"notification_url": invalid})
