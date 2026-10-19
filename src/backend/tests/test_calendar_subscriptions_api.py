"""Regular account endpoints: isolation, snapshot atomicity, overrides and refresh races."""

import asyncio
import time
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.api.routes import calendar_sources as routes
from src.core.config import settings
from src.core.crypto import decrypt_secret
from src.database.models.calendar_source import (
    CalendarSource,
    CalendarSourceEvent,
    CalendarSourceOverride,
)
from src.database.session import SessionLocal
from src.features import calendar_sources as service
from src.features.calendar_fetch import CalendarFetchError, CalendarSnapshot
from src.features.calendar_ics import parse_calendar
from tests.test_calendar_subscriptions_parser import feed
from tests.test_game_files_flow import game_flow  # noqa: F401

BASE = "/api/calendar/sources"
URL = "https://calendar.example.test/private/very-secret-token.ics"


def snapshot(title="Upstream", sequence=0, *, cancelled=False):
    components, warnings = parse_calendar(
        feed(
            f"UID:shared\r\nDTSTART:20261019T120000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:{title}\r\nSEQUENCE:{sequence}"
            + ("\r\nSTATUS:CANCELLED" if cancelled else "")
        )
    )
    return CalendarSnapshot("updated", components, warnings, '"v1"', None, "0" * 64)


@pytest.fixture(name="transport")
def transport_fixture(monkeypatch):
    state = {"snapshot": snapshot(), "calls": []}

    async def fetch(url, etag, modified):
        state["calls"].append((url, etag, modified))
        if isinstance(state["snapshot"], Exception):
            raise state["snapshot"]
        return state["snapshot"]

    monkeypatch.setattr(service, "refresh_snapshot", fetch)
    monkeypatch.setattr(routes, "refresh_snapshot", fetch)
    return state


async def add(flow, name="Shared calendar"):
    response = await flow.client.post(BASE, json={"name": name, "url": URL})
    assert response.status_code == 201, response.text
    return response.json()


async def events(flow, source):
    response = await flow.client.get(
        f"{BASE}/{source['id']}/events",
        params={"start": "2026-10-01", "end": "2026-10-31", "timezone": "UTC"},
    )
    assert response.status_code == 200, response.text
    return response.json()["events"]


async def unthrottle(source):
    async with SessionLocal() as db:
        row = await db.get(CalendarSource, source["id"])
        row.last_manual_at = 0
        await db.commit()


def target(event):
    return {
        "event_id": event["event_id"],
        "recurrence_key": event["recurrence_key"],
        "occurrence_date": event["original_start"]["value"][:10],
        "timezone": "UTC",
    }


async def test_secret_encrypted_source_cap_disabled_deployment_and_owner_404(
    flow, transport, monkeypatch
):
    source = await add(flow)
    assert "secret" not in str(source) and "url" not in source
    async with SessionLocal() as db:
        row = await db.get(CalendarSource, source["id"])
        assert row.url_encrypted != URL and decrypt_secret(row.url_encrypted) == URL
    for _ in range(9):
        await add(flow)
    assert (await flow.client.post(BASE, json={"name": "11th", "url": URL})).status_code == 409
    assert len((await flow.client.get(BASE)).json()["sources"]) == 10
    for suffix, method in [
        ("", "PATCH"),
        ("/refresh", "POST"),
        ("/events?start=2026-10-01&end=2026-10-31", "GET"),
        ("", "DELETE"),
    ]:
        response = await flow.client.request(
            method, f"{BASE}/{uuid4()}{suffix}", json={} if method == "PATCH" else None
        )
        assert response.status_code == 404
    monkeypatch.setattr(settings, "CALENDAR_SUBSCRIPTIONS_ENABLED", False)
    calls = len(transport["calls"])
    assert (await flow.client.post(BASE, json={"name": "Off", "url": URL})).status_code == 403
    assert (await flow.client.post(f"{BASE}/{source['id']}/refresh")).status_code == 403
    assert len(transport["calls"]) == calls
    assert len(await events(flow, source)) == 3  # cached data stays readable


async def test_create_failure_stores_nothing_and_invalid_urls_never_fetch(flow, transport):
    assert (
        await flow.client.post(BASE, json={"name": "Bad\u0000name", "url": URL})
    ).status_code == 422
    for url in [
        "http://public.example/a",
        "https://user:pass@public.example/a",
        "https://public.example:8443/a",
        "https://public.example/a#fragment",
    ]:
        assert (await flow.client.post(BASE, json={"name": "Bad", "url": url})).status_code == 422
    assert transport["calls"] == []
    transport["snapshot"] = CalendarFetchError("malformed")
    response = await flow.client.post(BASE, json={"name": "Bad", "url": URL})
    assert response.status_code == 400 and "secret-token" not in response.text
    assert (await flow.client.get(BASE)).json()["sources"] == []


async def test_all_day_overrides_require_whole_days_and_reject_control_text(flow, transport):
    components, warnings = parse_calendar(
        feed("UID:all-day\r\nDTSTART;VALUE=DATE:20261019\r\nDTEND;VALUE=DATE:20261022")
    )
    transport["snapshot"] = CalendarSnapshot("updated", components, warnings)
    source = await add(flow)
    first = (await events(flow, source))[0]
    for fields in [{"duration_seconds": 60}, {"duration_seconds": 0}, {"note": "bad\u0000notes"}]:
        response = await flow.client.put(
            f"{BASE}/{source['id']}/overrides",
            json={**target(first), "scope": "occurrence", "fields": fields},
        )
        assert response.status_code == 422, response.text
    response = await flow.client.put(
        f"{BASE}/{source['id']}/overrides",
        json={**target(first), "scope": "occurrence", "fields": {"duration_seconds": 86400}},
    )
    assert response.status_code == 200, response.text
    assert (await events(flow, source))[0]["last_date"] == "2026-10-19"
    assert (
        await flow.client.patch(f"{BASE}/{source['id']}", json={"name": "bad\u0000name"})
    ).status_code == 422


async def test_replay_stale_sequence_failure_and_304_keep_last_good_snapshot(flow, transport):
    source = await add(flow)
    first = await events(flow, source)
    response = await flow.client.post(f"{BASE}/{source['id']}/refresh")
    assert response.status_code == 200
    assert [e["event_id"] for e in await events(flow, source)] == [e["event_id"] for e in first]
    assert (await flow.client.post(f"{BASE}/{source['id']}/refresh")).status_code == 429
    for result, title in [
        (snapshot("New", 2), "New"),
        (snapshot("Stale", 1), "New"),
        (CalendarFetchError("truncated"), "New"),
        (CalendarSnapshot("not_modified"), "New"),
    ]:
        await unthrottle(source)
        transport["snapshot"] = result
        assert (await flow.client.post(f"{BASE}/{source['id']}/refresh")).status_code == 200
        assert [e["title"] for e in await events(flow, source)] == [title] * 3
    assert transport["calls"][1][1] == '"v1"'


async def test_series_occurrence_reset_refresh_cancellation_and_manual_copy(flow, transport):
    source = await add(flow)
    first = (await events(flow, source))[0]
    series = {
        **target(first),
        "scope": "series",
        "fields": {
            "title": "Series local",
            "start": {"kind": "utc", "value": "2026-10-19T13:00:00+00:00"},
        },
    }
    single = {
        **target(first),
        "scope": "occurrence",
        "fields": {"title": "One local", "location": "Local room"},
    }
    for value in [series, single]:
        response = await flow.client.put(f"{BASE}/{source['id']}/overrides", json=value)
        assert response.status_code == 200, response.text
    changed = await events(flow, source)
    assert [e["title"] for e in changed] == ["One local", "Series local", "Series local"]
    assert all(e["event_time"] == "13:00" for e in changed)
    assert [e["event_date"] for e in changed] == ["2026-10-19", "2026-10-20", "2026-10-21"]
    copy = await flow.client.post(f"{BASE}/{source['id']}/copy", json=target(first))
    assert copy.status_code == 201, copy.text
    transport["snapshot"] = snapshot("Updated upstream", 2)
    await flow.client.post(f"{BASE}/{source['id']}/refresh")
    assert [e["title"] for e in await events(flow, source)] == [
        "One local",
        "Series local",
        "Series local",
    ]
    assert (
        await flow.client.request("DELETE", f"{BASE}/{source['id']}/overrides", json=single)
    ).status_code == 200
    assert (await events(flow, source))[0]["title"] == "Series local"
    assert (
        await flow.client.request("DELETE", f"{BASE}/{source['id']}/overrides", json=series)
    ).status_code == 200
    assert (await events(flow, source))[0]["title"] == "Updated upstream"
    transport["snapshot"] = snapshot("Cancelled", 3, cancelled=True)
    await unthrottle(source)
    await flow.client.post(f"{BASE}/{source['id']}/refresh")
    assert await events(flow, source) == []
    manual = (await flow.client.get("/api/calendar/events")).json()
    assert [(e["title"], e["event_time"], e["note"]) for e in manual] == [
        ("One local", "13:00", "Location: Local room")
    ]
    await flow.client.delete(f"{BASE}/{source['id']}")
    async with SessionLocal() as db:
        assert not (
            await db.scalars(
                select(CalendarSourceEvent).where(CalendarSourceEvent.source_id == source["id"])
            )
        ).all()
        assert not (
            await db.scalars(
                select(CalendarSourceOverride).where(
                    CalendarSourceOverride.source_id == source["id"]
                )
            )
        ).all()
    assert len((await flow.client.get("/api/calendar/events")).json()) == 1


async def test_missing_success_tombstones_but_failed_snapshot_does_not_remove(flow, transport):
    source = await add(flow)
    transport["snapshot"] = CalendarFetchError("malformed")
    await flow.client.post(f"{BASE}/{source['id']}/refresh")
    assert len(await events(flow, source)) == 3
    transport["snapshot"] = CalendarSnapshot("updated")
    await unthrottle(source)
    await flow.client.post(f"{BASE}/{source['id']}/refresh")
    assert await events(flow, source) == []
    async with SessionLocal() as db:
        row = await db.scalar(
            select(CalendarSourceEvent).where(CalendarSourceEvent.source_id == source["id"])
        )
        assert row.removed_at is not None
        row.removed_at = int(time.time()) - 31 * 86400
        await db.commit()
    await service.run_due_sources()
    async with SessionLocal() as db:
        assert not (
            await db.scalars(
                select(CalendarSourceEvent).where(CalendarSourceEvent.source_id == source["id"])
            )
        ).all()


async def test_floating_series_shift_preserves_each_date_and_refuses_a_domain_change(flow, transport):
    components, warnings = parse_calendar(feed("UID:floating\r\nDTSTART:20261019T203000\r\nRRULE:FREQ=WEEKLY;COUNT=3"))
    transport["snapshot"] = CalendarSnapshot("updated", components, warnings)
    source = await add(flow)
    first = (await events(flow, source))[0]
    change = {**target(first), "scope": "series", "fields": {"start": {"kind": "floating", "value": "2026-10-19T21:00:00"}}}
    response = await flow.client.put(f"{BASE}/{source['id']}/overrides", json=change)
    assert response.status_code == 200, response.text
    shifted = await events(flow, source)
    assert [(e["event_date"], e["event_time"]) for e in shifted] == [("2026-10-19", "21:00"), ("2026-10-26", "21:00")]
    change["fields"]["start"] = {"kind": "utc", "value": "2026-10-19T21:00:00+00:00"}
    assert (await flow.client.put(f"{BASE}/{source['id']}/overrides", json=change)).status_code == 422
    assert await events(flow, source) == shifted


async def test_disabled_or_replaced_source_does_not_accept_an_inflight_result(
    flow, transport, monkeypatch
):
    source = await add(flow)
    started, finish = asyncio.Event(), asyncio.Event()

    async def delayed(*_args):
        started.set()
        await finish.wait()
        return snapshot("Late", 10)

    monkeypatch.setattr(service, "refresh_snapshot", delayed)
    task = asyncio.create_task(flow.client.post(f"{BASE}/{source['id']}/refresh"))
    await asyncio.wait_for(started.wait(), 5)
    assert (await flow.client.post(f"{BASE}/{source['id']}/refresh")).status_code == 409
    assert (
        await flow.client.patch(f"{BASE}/{source['id']}", json={"enabled": False})
    ).status_code == 200
    finish.set()
    assert (await task).status_code == 200
    assert await events(flow, source) == []
    await flow.client.patch(f"{BASE}/{source['id']}", json={"enabled": True})
    assert (await events(flow, source))[0]["title"] == "Upstream"


async def test_real_regular_session_anonymous_and_integration_credentials(flow, transport):
    import httpx

    from src.core.auth import get_current_user
    from src.main import app

    source = await add(flow)
    override = app.dependency_overrides.pop(get_current_user)
    try:
        assert (await flow.client.get(BASE)).status_code == 200  # real non-admin session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as anonymous:
            assert (await anonymous.get(BASE)).status_code == 401
            assert (
                await anonymous.get(BASE, headers={"Authorization": "Bearer utp_example"})
            ).status_code == 403
            assert (
                await anonymous.get(BASE, headers={"Authorization": "Bearer utd_example"})
            ).status_code == 403
    finally:
        app.dependency_overrides[get_current_user] = override
    assert len(await events(flow, source)) == 3


async def test_another_users_existing_source_is_404_before_remote_work(flow, transport):
    from sqlalchemy import delete

    from src.core.crypto import encrypt_secret
    from src.database.models.user import User

    async with SessionLocal() as db:
        other = User(
            username="calendar_" + uuid4().hex,
            email=f"{uuid4()}@example.test",
            password_hash="unused",
        )
        db.add(other)
        await db.flush()
        user_id = other.id
        source = CalendarSource(
            user_id=user_id,
            name="Private",
            hostname="example.test",
            url_encrypted=encrypt_secret(URL),
        )
        db.add(source)
        await db.commit()
        source_id = source.id
    try:
        for method, suffix, body in [
            ("GET", "/events?start=2026-10-01&end=2026-10-31", None),
            ("POST", "/refresh", None),
            ("PATCH", "", {"url": URL}),
            ("DELETE", "", None),
        ]:
            response = await flow.client.request(method, f"{BASE}/{source_id}{suffix}", json=body)
            assert response.status_code == 404, response.text
        assert transport["calls"] == []
        assert (await flow.client.get(BASE)).json()["sources"] == []
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


async def test_published_feeds_exclude_subscriptions_and_scheduling_rechecks_due_time(
    flow, transport
):
    from src.database.models.user import User

    source = await add(flow)
    token = uuid4().hex
    async with SessionLocal() as db:
        owner = await db.get(User, flow.user_id)
        owner.calendar_token = token
        await db.commit()
    for suffix in [".ics", "/manual.ics", "/anime.ics"]:
        response = await flow.client.get(f"/api/calendar/feed/{token}{suffix}")
        assert response.status_code == 200
        assert "Upstream" not in response.text
    calls = len(transport["calls"])
    async with SessionLocal() as db:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as not_due:
            await service.refresh_source(db, source["id"], flow.user_id, manual=False)
        assert not_due.value.status_code == 409
    assert len(transport["calls"]) == calls
