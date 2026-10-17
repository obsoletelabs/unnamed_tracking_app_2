"""Timing policy applied to real routing: holds, mute, disabled routes, isolation and the preview."""

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from src.api.routes.notification_timing import router as timing_router
from src.core.auth import get_current_user
from src.core.preferences import load_preferences, save_preferences
from src.database.models.notification import Notification
from src.database.models.notification_aggregation import NotificationAggregationWindow
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User
from src.database.session import SessionLocal, get_db
from src.features.notification_policy import INBOX_PROVIDER
from src.features.notification_providers.delivery import process_pending_deliveries
from tests.test_notification_aggregation import (
    account as account,
)
from tests.test_notification_aggregation import (
    deliveries,
    drain,
    emit,
    media_row,
    release,
)
from tests.test_notification_aggregation import (
    transport as transport,
)


def clock(offset_minutes):
    value = datetime.now(UTC) + timedelta(minutes=offset_minutes)
    return value.strftime("%H:%M")


def around_now():
    """Quiet hours (UTC) that contain the current minute, whatever time the suite runs."""
    return {"enabled": True, "start": clock(-90), "end": clock(90)}


async def destination_of(account):
    async with SessionLocal() as db:
        return await db.scalar(
            select(NotificationDestination).where(
                NotificationDestination.user_id == account,
                NotificationDestination.provider_id == "a",
            )
        )


async def configure(account, **timing):
    async with SessionLocal() as db:
        await save_preferences(db, account, {"notification_timing": {"timezone": "UTC", **timing}})


async def set_route(account, event_type, destination, **choice):
    async with SessionLocal() as db:
        routes = (await load_preferences(db, account))["notification_routes"]
        routes = {
            **routes,
            event_type: {str(destination.id): {"enabled": True, "urgency": "normal", **choice}},
        }
        await save_preferences(db, account, {"notification_routes": routes})


async def test_quiet_hours_hold_external_delivery_in_one_window_and_never_the_inbox(
    account, transport
):
    await configure(account, quiet_hours=around_now())
    ids = await emit(account, media_row(), media_row("movie_released"), media_row("game_released"))
    held = await deliveries(ids)
    assert {work.status for work in held} == {"aggregating"}
    assert len({work.window_id for work in held}) == 1
    assert {work.timing_reason for work in held} == {"quiet"}
    async with SessionLocal() as db:
        window = await db.get(NotificationAggregationWindow, held[0].window_id)
        assert window.strategy == "digest" and window.scope_key.startswith("timing:")
        assert window.window_end > time.time() + 60 * 60  # waits for the end of quiet hours
        inbox = await db.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id.in_(ids),
                NotificationDelivery.provider_id == INBOX_PROVIDER,
            )
        )
        assert {row.status for row in inbox} == {"sent"}
        assert await drain(db) == 0
    assert transport.messages == []


async def test_released_hold_sends_one_digest_with_every_item(account, transport):
    await configure(account, quiet_hours=around_now())
    ids = await emit(account, *(media_row(title=f"Show {n}") for n in range(3)))
    async with SessionLocal() as db:
        await release(db)
        assert await drain(db) == 1
    assert len(transport.messages) == 1
    assert {item.id for item in transport.messages[0].items} == set(ids)
    assert {work.status for work in await deliveries(ids)} == {"sent"}


async def test_without_a_timing_document_behaviour_is_unchanged(account, transport):
    ids = await emit(account, media_row("game_price_hit"), media_row())
    statuses = {work.status for work in await deliveries(ids)}
    assert statuses == {"pending", "aggregating"}  # price alert immediate, episode coalesced


async def test_quiet_hours_do_not_delay_security_traffic(account, transport):
    await configure(account, quiet_hours=around_now())
    ids = await emit(account, media_row("session_anomaly"))
    assert (await deliveries(ids))[0].status == "pending"


async def test_urgent_route_request_bypasses_quiet_hours_but_not_when_user_limits_it(
    account, transport
):
    destination = await destination_of(account)
    await set_route(account, "media.movie.released", destination, urgency="critical")
    await configure(account, quiet_hours=around_now())
    urgent = await emit(account, media_row("movie_released"))
    assert (await deliveries(urgent))[0].status == "pending"
    await configure(account, quiet_hours=around_now(), urgent_threshold="off")
    limited = await emit(account, media_row("movie_released"))
    assert (await deliveries(limited))[0].status == "aggregating"


async def test_plugin_urgency_request_needs_the_users_opt_in(account, transport):
    from src.features.notification_timing import decide_for

    destination = await destination_of(account)
    notice = Notification(
        event_type="demo.note",
        kind="plugin",
        purpose="standard",
        severity="info",
        requested_urgency="critical",
        source_installation_id=uuid4(),
        media_id=uuid4(),
        event_at=int(time.time()),
    )
    now = int(time.time())
    async with SessionLocal() as db:
        await save_preferences(
            db, account, {"notification_timing": {"timezone": "UTC", "quiet_hours": around_now()}}
        )
        prefs = await load_preferences(db, account)
        assert decide_for(notice, destination, prefs, now=now).action == "hold"
        await save_preferences(
            db,
            account,
            {
                "notification_timing": {
                    "timezone": "UTC",
                    "quiet_hours": around_now(),
                    "plugin_urgent_bypass": True,
                }
            },
        )
        prefs = await load_preferences(db, account)
        assert decide_for(notice, destination, prefs, now=now).bypassed


async def test_mute_creates_no_external_work_but_keeps_inbox_history(account, transport):
    await configure(account, rules=[{"category": "media", "mode": "mute"}])
    ids = await emit(account, media_row(), media_row("game_released"))
    external = await deliveries(ids)
    assert [work.notification_id for work in external] == [ids[1]]  # only the game, not media
    async with SessionLocal() as db:
        inbox = await db.scalar(
            select(func.count(NotificationDelivery.id)).where(
                NotificationDelivery.notification_id.in_(ids),
                NotificationDelivery.provider_id == INBOX_PROVIDER,
            )
        )
        assert inbox == 2


async def test_mute_chosen_after_queueing_suppresses_held_work_without_sending(account, transport):
    await configure(account, quiet_hours=around_now())
    ids = await emit(account, media_row(), media_row())
    await configure(account, rules=[{"category": "*", "mode": "mute"}])
    async with SessionLocal() as db:
        await release(db)
        assert await drain(db) == 0
    assert transport.messages == []
    work = await deliveries(ids)
    assert {(item.status, item.last_error) for item in work} == {("suppressed", "muted")}


async def test_disabled_route_queues_nothing_and_re_enabling_does_not_replay(account, transport):
    destination = await destination_of(account)
    await configure(account, quiet_hours=around_now())
    async with SessionLocal() as db:
        await save_preferences(
            db,
            account,
            {
                "notification_routes": {
                    "media.episode.aired": {
                        str(destination.id): {"enabled": False, "urgency": "normal"}
                    }
                }
            },
        )
    skipped = await emit(account, media_row())
    assert await deliveries(skipped) == []
    await set_route(account, "media.episode.aired", destination)
    later = await emit(account, media_row())
    assert len(await deliveries(later)) == 1
    assert await deliveries(skipped) == []  # no replay of what was disabled at the time


async def test_held_work_is_dropped_when_the_route_is_disabled_before_release(account, transport):
    destination = await destination_of(account)
    await configure(account, quiet_hours=around_now())
    ids = await emit(account, media_row())
    async with SessionLocal() as db:
        await save_preferences(
            db,
            account,
            {
                "notification_routes": {
                    "media.episode.aired": {
                        str(destination.id): {"enabled": False, "urgency": "normal"}
                    }
                }
            },
        )
        await release(db)
        assert await drain(db) == 0
    assert transport.messages == []
    assert {(work.status, work.last_error) for work in await deliveries(ids)} == {
        ("suppressed", "routing_changed")
    }


async def test_provider_disable_and_re_enable_does_not_replay_skipped_events(account, transport):
    await configure(account, quiet_hours=around_now())

    async def provider(enabled):
        async with SessionLocal() as db:
            row = await db.scalar(
                select(NotificationProviderSetting).where(
                    NotificationProviderSetting.user_id == account,
                    NotificationProviderSetting.provider_id == "a",
                )
            )
            row.enabled = enabled
            await db.commit()

    await provider(False)
    skipped = await emit(account, media_row())
    assert await deliveries(skipped) == []
    await provider(True)
    fresh = await emit(account, media_row())
    assert (await deliveries(fresh))[0].status == "aggregating"
    assert await deliveries(skipped) == []


async def test_each_user_has_their_own_schedule(account, transport):
    other = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=other,
                username=f"o_{other.hex}",
                email=f"{other}@example.test",
                password_hash="x",
            )
        )
        await db.commit()
    try:
        await configure(account, quiet_hours=around_now())
        async with SessionLocal() as db:
            assert (await load_preferences(db, other))["notification_timing"]["quiet_hours"][
                "enabled"
            ] is False
    finally:
        async with SessionLocal() as db:
            await db.delete(await db.get(User, other))
            await db.commit()


def api(db, user_id):
    app = FastAPI()
    app.include_router(timing_router)

    async def authenticated_user():
        return await db.get(User, user_id)

    app.dependency_overrides[get_current_user] = authenticated_user
    app.dependency_overrides[get_db] = lambda: db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_preview_explains_the_decision_and_isolates_users(account, transport):
    destination = await destination_of(account)
    await configure(account, quiet_hours=around_now())
    other = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=other,
                username=f"p_{other.hex}",
                email=f"{other}@example.test",
                password_hash="x",
            )
        )
        await db.commit()
    try:
        async with SessionLocal() as db:
            path = "/api/settings/notification-timing/preview"
            query = {"event_type": "media.episode.aired", "destination_id": str(destination.id)}
            async with api(db, account) as client:
                body = (await client.get(path, params=query)).json()
                assert body["decision"]["action"] == "hold"
                assert body["decision"]["release_at"] > time.time()
                assert body["decision"]["reasons"]
                urgent = (await client.get(path, params={**query, "severity": "critical"})).json()
                assert urgent["decision"]["bypassed_by_urgency"] is True
            async with api(db, other) as client:
                assert (await client.get(path, params=query)).status_code == 404
    finally:
        async with SessionLocal() as db:
            await db.delete(await db.get(User, other))
            await db.commit()


async def test_preview_of_a_disabled_route_reports_nothing_queued(account, transport):
    destination = await destination_of(account)
    async with SessionLocal() as db:
        await save_preferences(
            db,
            account,
            {
                "notification_routes": {
                    "media.episode.aired": {
                        str(destination.id): {"enabled": False, "urgency": "normal"}
                    }
                }
            },
        )
        async with api(db, account) as client:
            body = (
                await client.get(
                    "/api/settings/notification-timing/preview",
                    params={
                        "event_type": "media.episode.aired",
                        "destination_id": str(destination.id),
                    },
                )
            ).json()
    assert body["route_enabled"] is False and body["decision"]["action"] == "none"


async def test_timing_document_validation_reaches_the_preference_store(account):
    async with SessionLocal() as db:
        with pytest.raises(ValueError):
            await save_preferences(
                db, account, {"notification_timing": {"timezone": "Nowhere/Land"}}
            )
        saved = await save_preferences(
            db, account, {"notification_timing": {"timezone": "Europe/Paris"}}
        )
        assert saved["notification_timing"]["timezone"] == "Europe/Paris"
        assert saved["notification_timing"]["quiet_hours"]["enabled"] is False


async def test_process_pending_is_idempotent_with_no_work(account, transport):
    async with SessionLocal() as db:
        assert await process_pending_deliveries(db) == 0
