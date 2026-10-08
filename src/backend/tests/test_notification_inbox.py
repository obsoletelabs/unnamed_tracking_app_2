"""Inbox counts/pagination, owner isolation and visible retention policy."""

import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete

from src.core.preferences import load_preferences, save_preferences
from src.database.models.notification import Notification
from src.database.models.user import User
from src.database.models.user_preferences import UserPreferences
from src.database.session import SessionLocal
from src.features.notification_inbox import InboxQuery, query_inbox, retention_policy
from src.features.notifications import generate_for_user


@pytest.fixture
async def inbox_owner():
    async with SessionLocal() as db:
        owner = User(
            username=f"inbox_{uuid4().hex}", email=f"{uuid4()}@example.test", password_hash="unused"
        )
        stranger = User(
            username=f"inbox_{uuid4().hex}", email=f"{uuid4()}@example.test", password_hash="unused"
        )
        db.add_all([owner, stranger])
        await db.commit()
        identities = owner.id, stranger.id
    yield identities
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_(identities)))
        await db.commit()


def notification(owner, kind="game_sale", **changes):
    fields = dict(
        user_id=owner,
        kind=kind,
        event_type="game.sale.started",
        source="store.example",
        title="Sale 50% off",
        body="Game on sale",
        media_type="game",
        media_id=uuid4(),
        event_at=int(time.time()),
        dedupe_key=str(uuid4()),
        inbox_visible=True,
    )
    return Notification(**{**fields, **changes})


async def test_counts_cover_pages_filters_and_owner_scope(inbox_owner):
    owner, stranger = inbox_owner
    async with SessionLocal() as db:
        notices = [
            notification(owner),
            notification(owner, read_at=int(time.time())),
            notification(owner, "session_anomaly", severity="warning"),
            notification(owner, dismissed_at=int(time.time())),
            notification(stranger),
        ]
        db.add_all(notices)
        await db.commit()
        first, summary = await query_inbox(db, owner, InboxQuery(limit=1))
        assert len(first) == 1
        assert summary == dict(
            total=3,
            unread=2,
            counts=dict(all=3, unread=2, episodes=0, seasons=0, releases=2, security=1, plugins=0),
            next_offset=1,
            sources=["store.example"],
        )
        second, _ = await query_inbox(db, owner, InboxQuery(limit=1, offset=1))
        assert first[0].id != second[0].id
        sales, summary = await query_inbox(
            db, owner, InboxQuery(category="releases", unread_only=True)
        )
        assert len(sales) == summary["total"] == 1
        assert sales[0].read_at is None
        assert all(row.user_id == owner for row in sales)
        warning, _ = await query_inbox(db, owner, InboxQuery(severity="warning"))
        assert [row.kind for row in warning] == ["session_anomaly"]
        literal, _ = await query_inbox(db, owner, InboxQuery(search="50%"))
        assert len(literal) == 3
        absent, summary = await query_inbox(db, owner, InboxQuery(search="%_", source="unknown"))
        assert not absent and summary["total"] == 0


async def test_http_query_metadata_and_validation(inbox_owner):
    from src.core.auth import get_current_user
    from src.main import app

    owner, _ = inbox_owner
    async with SessionLocal() as db:
        db.add(notification(owner))
        await db.commit()
        user = await db.get(User, owner)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/notifications?category=releases&limit=1")
            assert response.status_code == 200
            body = response.json()
            assert body["total"] == 1 and body["next_offset"] is None
            assert body["items"][0]["source"] == "store.example"
            assert body["items"][0]["required_trust"] == 1
            assert (await client.get("/api/notifications?limit=201")).status_code == 422
            assert (await client.get("/api/notifications?category=invalid")).status_code == 422
            assert (await client.get("/api/notifications/policy")).status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_existing_retention_choice_survives_server_defaults_and_cap(inbox_owner, monkeypatch):
    from src.core.config import settings

    owner, _ = inbox_owner
    monkeypatch.setattr(settings, "NOTIFICATION_RETENTION_DEFAULT_DAYS", 180)
    monkeypatch.setattr(settings, "NOTIFICATION_RETENTION_MAXIMUM_DAYS", 90)
    async with SessionLocal() as db:
        initial = await load_preferences(db, owner)
        assert retention_policy(initial)["effective_days"] == 90
        db.add(UserPreferences(user_id=owner, data={"notification_retention_days": 365}))
        await db.commit()
        existing = await load_preferences(db, owner)
        assert not existing["notification_retention_inherit"]
        assert existing["notification_retention_days"] == 365
        policy = retention_policy(existing)
        assert (
            policy["limited"] and policy["requested_days"] == 365 and policy["effective_days"] == 90
        )
        saved = await save_preferences(db, owner, {"notification_retention_days": 0})
        assert (
            not saved["notification_retention_inherit"]
            and retention_policy(saved)["effective_days"] == 90
        )
        saved = await save_preferences(db, owner, {"notification_retention_inherit": True})
        assert retention_policy(saved)["requested_days"] == 180
        monkeypatch.setattr(settings, "NOTIFICATION_RETENTION_MAXIMUM_DAYS", 0)
        assert retention_policy(saved)["effective_days"] == 180


async def test_expiry_does_not_reappear_or_cross_account(inbox_owner, monkeypatch):
    from src.core.config import settings
    from src.database.models.notification_receipt import NotificationReceipt

    owner, stranger = inbox_owner
    monkeypatch.setattr(settings, "NOTIFICATION_RETENTION_MAXIMUM_DAYS", 1)
    async with SessionLocal() as db:
        expired = notification(owner, event_at=int(time.time()) - 2 * 86400)
        other = notification(stranger, event_at=expired.event_at)
        db.add_all([expired, other])
        db.add(
            NotificationReceipt(
                user_id=owner,
                dedupe_key=expired.dedupe_key,
                event_type=expired.event_type,
                source=expired.source,
                occurred_at=expired.event_at,
            )
        )
        await db.commit()
        await generate_for_user(db, owner)
        assert await db.get(Notification, expired.id, populate_existing=True) is None
        assert await db.get(Notification, other.id, populate_existing=True) is not None
        assert (await query_inbox(db, owner, InboxQuery()))[1]["total"] == 0


async def test_routing_metadata_is_owner_scoped_secret_free_and_policy_checked(inbox_owner):
    from src.core.auth import get_current_user
    from src.database.models.notification_destination import NotificationDestination
    from src.features.notification_policy import INBOX_PROVIDER
    from src.main import app

    owner, stranger = inbox_owner
    async with SessionLocal() as db:
        target = NotificationDestination(
            user_id=owner,
            provider_id="retired.provider",
            endpoint_key="private-address",
            kind="legacy_webhook",
            channel_context="external",
            privacy=0,
            active=False,
            configuration_ref="secret-credential-reference",
            installation_id=uuid4(),
        )
        hidden = NotificationDestination(
            user_id=stranger,
            provider_id="hidden.provider",
            endpoint_key="stranger-address",
            kind="email",
            channel_context="external",
            privacy=1,
        )
        db.add_all([target, hidden])
        await db.commit()
        user = await db.get(User, owner)
        target_id, hidden_id = str(target.id), str(hidden.id)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/notification-providers/destinations")
            assert response.status_code == 200
            metadata = response.json()
            destinations = {row["id"]: row for row in metadata["destinations"]}
            assert hidden_id not in destinations
            assert "hidden.provider" not in response.text
            assert "secret-credential-reference" not in response.text
            assert (
                "private-address" not in response.text and "stranger-address" not in response.text
            )
            retired = destinations[target_id]
            assert not retired["active"] and not retired["available"]
            assert retired["trust"] == "PUBLIC"
            assert "security.session.anomaly" not in retired["eligible_types"]
            assert "game.sale.started" in retired["eligible_types"]
            inbox = next(
                row for row in destinations.values() if row["provider_id"] == INBOX_PROVIDER
            )
            assert (
                inbox["trust"] == "SECURE" and "security.session.anomaly" in inbox["eligible_types"]
            )
            # Owner preference changes cannot affect a stranger's real destination.
            response = await client.patch(
                "/api/preferences",
                json={
                    "notification_routes": {
                        "game.sale.started": {hidden_id: {"enabled": False, "urgency": "critical"}}
                    }
                },
            )
            assert response.status_code == 200
        async with SessionLocal() as db:
            assert (await load_preferences(db, stranger))["notification_routes"] == {}
            assert (await db.get(NotificationDestination, hidden_id)).enabled
    finally:
        app.dependency_overrides.pop(get_current_user, None)
