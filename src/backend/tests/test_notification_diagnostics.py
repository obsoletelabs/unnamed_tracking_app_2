"""Notification diagnostics: permissions, redaction, dry-run isolation, previews and test sends."""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update

from src.api.routes.notification_diagnostics import router
from src.core.auth import get_current_user
from src.database.models.notification import Notification
from src.database.models.notification_aggregation import NotificationAggregationWindow
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_delivery_attempt import NotificationDeliveryAttempt
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.notification_story import NotificationStory
from src.database.models.user import User
from src.database.session import SessionLocal, get_db
from src.features import notification_diagnostics as diagnostics
from src.features.notification_diagnostic_fixtures import FIXTURES, reason, redact
from src.features.notification_providers.delivery import MAX_ATTEMPTS, process_pending_deliveries
from src.plugin_api.notification_story_contracts import (
    NOTIFICATION_CONTRACT_VERSION,
    ImageCapabilities,
    NotificationProviderCapabilities,
)
from tests.test_notification_aggregation import (
    Transport,
    add_destination,
)
from tests.test_notification_aggregation import (
    account as account,
)
from tests.test_notification_aggregation import (
    transport as transport,
)

REGISTRY = "src.features.notification_diagnostics.get_notification_providers"
BASE = "/api/settings/notification-diagnostics"
TABLES = (
    Notification,
    NotificationDelivery,
    NotificationDeliveryAttempt,
    NotificationReceipt,
    NotificationStory,
    NotificationAggregationWindow,
)


def client(db, user_id):
    app = FastAPI()
    app.include_router(router)

    async def authenticated():
        return await db.get(User, user_id)

    app.dependency_overrides[get_current_user] = authenticated
    app.dependency_overrides[get_db] = lambda: db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def counts():
    async with SessionLocal() as db:
        return [await db.scalar(select(func.count()).select_from(table)) for table in TABLES]  # pylint: disable=not-callable


async def make_admin(user_id, value=True):
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.id == user_id).values(is_admin=value))
        await db.commit()


@pytest.fixture
async def other():
    user_id = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                username=f"other_{user_id.hex}",
                email=f"{user_id}@example.test",
                password_hash="unused",
            )
        )
        await db.commit()
    yield user_id
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


async def post(account, path, body, admin=True):
    await make_admin(account, admin)
    async with SessionLocal() as db:
        async with client(db, account) as http:
            return await http.post(f"{BASE}{path}", json=body)


async def get(account, path, admin=True, **params):
    await make_admin(account, admin)
    async with SessionLocal() as db:
        async with client(db, account) as http:
            return await http.get(f"{BASE}{path}", params=params)


def stages(body, destination=0):
    return {s["stage"]: s for s in body["stages"]} | {
        s["stage"]: s for s in body["destinations"][destination]["stages"]
    }


# Permissions ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/overview", None),
        ("post", "/simulate", {"fixture": "standard"}),
        ("post", "/preview", {"variant": "rich"}),
        ("post", "/digest", {"event_type": "game.sale.started", "provider_id": "core.smtp"}),
        ("post", "/test-send", {"destination_id": str(uuid4()), "confirm": True}),
    ],
)
async def test_administrator_tools_refuse_ordinary_users(account, transport, method, path, body):
    await make_admin(account, False)
    async with SessionLocal() as db:
        async with client(db, account) as http:
            response = await http.request(method, f"{BASE}{path}", json=body)
    assert response.status_code == 403


async def test_ordinary_users_keep_their_own_delivery_history(account, transport):
    response = await get(account, "/deliveries", admin=False)
    assert response.status_code == 200
    assert response.json()["retry_policy"]["max_attempts"] == MAX_ATTEMPTS


async def test_unknown_fields_and_fixtures_are_rejected(account, transport):
    assert (
        await post(account, "/simulate", {"fixture": "standard", "title": "x"})
    ).status_code == 422
    assert (await post(account, "/simulate", {"fixture": "nope"})).status_code == 404
    assert (await post(account, "/test-send", {"destination_id": str(uuid4())})).status_code == 422


async def test_simulation_never_reads_another_accounts_destinations(account, transport, other):
    async with SessionLocal() as db:
        foreign = await add_destination(db, other, "a")
        await db.commit()
    body = (
        await post(account, "/simulate", {"fixture": "standard", "destination_id": str(foreign.id)})
    ).json()
    assert body["destinations"] == []
    assert body["stages"][-1]["code"] == "no_destination"
    assert str(foreign.id) not in json.dumps(body)


# Dry runs -------------------------------------------------------------------------------------


async def test_every_dry_run_leaves_no_rows_behind(account, transport, monkeypatch):
    monkeypatch.setattr(REGISTRY, AsyncMock(return_value={"a": transport}))
    before = await counts()
    for fixture in FIXTURES:
        assert (await post(account, "/simulate", {"fixture": fixture})).status_code == 200
    for variant in ("basic", "rich", "image", "actions", "long"):
        assert (await post(account, "/preview", {"variant": variant})).status_code == 200
    digest = {"event_type": "game.sale.started", "provider_id": "core.smtp", "count": 80}
    assert (await post(account, "/digest", digest)).status_code == 200
    assert (await get(account, "/overview")).status_code == 200
    assert await counts() == before


async def test_trace_covers_ingestion_grouping_timing_eligibility_and_delivery(
    account, transport, monkeypatch
):
    monkeypatch.setattr(REGISTRY, AsyncMock(return_value={"a": transport}))
    body = (await post(account, "/simulate", {"fixture": "standard"})).json()
    assert body["dry_run"] is True
    found = stages(body)
    assert set(found) >= {
        "ingestion",
        "grouping",
        "timing",
        "eligibility",
        "capability",
        "delivery",
    }
    assert found["ingestion"]["code"] == "accepted"
    assert found["grouping"]["code"] == "coalesce"
    assert found["eligibility"]["code"] == "eligible"
    assert (
        found["delivery"]["detail"]["status"] == "held"
    )  # coalescing window, not an immediate send
    assert all(s["label"] and s["action"] for s in body["stages"])


async def test_urgency_bypasses_grouping_and_quiet_hours(account, transport, monkeypatch):
    monkeypatch.setattr(REGISTRY, AsyncMock(return_value={"a": transport}))
    quiet = stages((await post(account, "/simulate", {"fixture": "quiet_hours"})).json())
    assert quiet["timing"]["code"] == "quiet_hours"
    assert quiet["timing"]["detail"]["release_at"] > time.time()
    urgent = stages((await post(account, "/simulate", {"fixture": "urgent"})).json())
    assert urgent["grouping"]["code"] == "urgent_bypass"
    assert urgent["timing"]["detail"]["bypassed_by_urgency"] is True
    assert urgent["delivery"]["detail"]["status"] == "sent"
    both = stages(
        (
            await post(account, "/simulate", {"fixture": "quiet_hours", "severity": "critical"})
        ).json()
    )
    assert both["timing"]["detail"]["bypassed_by_urgency"] is True


async def test_duplicates_and_story_transitions_use_the_production_decisions(
    account, transport, monkeypatch
):
    monkeypatch.setattr(REGISTRY, AsyncMock(return_value={"a": transport}))
    duplicate = (await post(account, "/simulate", {"fixture": "duplicate_ingestion"})).json()
    assert [s["code"] for s in duplicate["stages"] if s["stage"] == "ingestion"] == [
        "accepted",
        "deduplicated",
        "deduplicated",
    ]
    story = (await post(account, "/simulate", {"fixture": "story_transitions"})).json()
    assert [s["code"] for s in story["stages"] if s["stage"] == "ingestion"] == [
        "accepted",
        "create_story",
        "update_in_place",
        "update_in_place",
        "update_in_place",
        "reject_closed",
    ]


@pytest.mark.parametrize(
    ("fixture", "final", "codes", "waits"),
    [
        ("provider_transient", "sent", ["provider_error", "provider_error", "sent"], [60, 180]),
        (
            "provider_rate_limited",
            "failed_permanent",
            ["rate_limited", "rate_limited", "attempts_exhausted"],
            [60, 180],
        ),
        ("provider_invalid_config", "failed_permanent", ["invalid_config"], []),
        ("provider_permanent", "failed_permanent", ["failed_permanent"], []),
        ("provider_unavailable", "retry_wait", ["provider_unavailable"], [60]),
    ],
)
async def test_failures_simulate_the_dispatchers_retry_machine(
    account, transport, monkeypatch, fixture, final, codes, waits
):
    monkeypatch.setattr(REGISTRY, AsyncMock(return_value={"a": transport}))
    # Urgent delivery is immediate, so the delivery stage reaches the retry machine.
    body = (await post(account, "/simulate", {"fixture": fixture, "severity": "critical"})).json()
    delivery = stages(body)["delivery"]["detail"]
    assert delivery["final_status"] == final
    assert [a["code"] for a in delivery["attempts"]] == codes
    assert all(a["action"] for a in delivery["attempts"])
    backoffs = [
        a["next_attempt_at"] - body["evaluated_at"]
        for a in delivery["attempts"]
        if "next_attempt_at" in a
    ]
    assert backoffs == waits


async def test_sensitive_flows_are_explained_but_never_rendered_or_sent(
    account, transport, monkeypatch
):
    monkeypatch.setattr(REGISTRY, AsyncMock(return_value={"a": transport}))
    before = await counts()
    body = (await post(account, "/simulate", {"fixture": "recovery"})).json()
    eligibility = stages(body)["eligibility"]
    assert eligibility["code"] == "sensitive_not_permitted"
    assert stages(body)["delivery"]["detail"]["status"] == "not_queued"
    preview = (await post(account, "/preview", {"fixture": "recovery"})).json()
    assert preview["redacted"] is True and preview["providers"] == []
    async with SessionLocal() as db:
        destination = await db.scalar(
            select(NotificationDestination).where(NotificationDestination.user_id == account)
        )
    refused = await post(
        account,
        "/test-send",
        {"destination_id": str(destination.id), "fixture": "verification", "confirm": True},
    )
    assert refused.status_code == 422
    assert await counts() == before


async def test_disabled_route_and_unverified_destination_give_actionable_reasons(
    account, transport, monkeypatch
):
    monkeypatch.setattr(REGISTRY, AsyncMock(return_value={"a": transport}))
    async with SessionLocal() as db:
        await db.execute(
            update(NotificationDestination)
            .where(NotificationDestination.user_id == account)
            .values(verification_method=None, privacy=0)
        )
        await db.commit()
    body = (await post(account, "/simulate", {"fixture": "standard"})).json()
    denial = body["destinations"][0]["stages"][0]
    assert denial["code"] == "trust_too_low" and "Verify" in denial["action"]
    assert body["destinations"][0]["stages"][-1]["detail"]["status"] == "not_queued"


# Previews -------------------------------------------------------------------------------------


def rich_provider():
    return SimpleNamespace(
        id="rich",
        name="Rich",
        transport=None,
        critical_supported=False,
        capabilities=NotificationProviderCapabilities(
            contract_version=NOTIFICATION_CONTRACT_VERSION,
            links=True,
            action_buttons=1,
            max_title_chars=40,
            max_text_chars=200,
            images=ImageCapabilities(max_images=1, external_urls=True),
        ),
        edit=None,
    )


async def preview_of(account, variant, monkeypatch, provider_id="rich"):
    monkeypatch.setattr(
        REGISTRY, AsyncMock(return_value={"rich": rich_provider(), "x": Transport()})
    )
    body = (
        await post(account, "/preview", {"variant": variant, "provider_id": provider_id})
    ).json()
    return body["providers"][0]


async def test_previews_apply_each_providers_declared_capabilities(account, monkeypatch):
    monkeypatch.setattr("src.core.config.settings.NOTIFICATION_IMAGE_MODE", "external")
    actions = await preview_of(account, "actions", monkeypatch)
    assert actions["renderer"] == "canonical"
    assert actions["actions"] == [
        {"label": "Open notifications", "url": "https://tracker.example/notifications"}
    ]
    assert "actions_via_story_link" in {f["code"] for f in actions["fallbacks"]}
    assert actions["link"] == "https://tracker.example/notifications"
    image = await preview_of(account, "image", monkeypatch)
    assert image["images"] == ["https://example.com/diagnostics/poster.png"]
    long = await preview_of(account, "long", monkeypatch)
    assert len(long["body"]) <= 200 and "truncated" in {f["code"] for f in long["fallbacks"]}
    assert long["limits"]["text_chars"] == 200
    legacy = await preview_of(account, "rich", monkeypatch, provider_id="x")
    assert legacy["renderer"] == "legacy"
    assert "rich_content_plain_text" in {f["code"] for f in legacy["fallbacks"]}


async def test_email_preview_is_html_and_never_active_content(account):
    body = (
        await post(account, "/preview", {"variant": "actions", "provider_id": "core.smtp"})
    ).json()
    rendered = body["providers"][0]
    assert rendered["provider_id"] == "core.smtp" and "<script" not in (rendered["html"] or "")


async def test_digest_dry_run_chunks_by_provider_limits(account):
    body = (
        await post(
            account,
            "/digest",
            {"event_type": "game.sale.started", "provider_id": "core.browser", "count": 120},
        )
    ).json()
    assert body["strategy"] == "digest" and body["window_end"] > body["window_start"]
    assert sum(chunk["items"] for chunk in body["chunks"]) == 120
    assert len(body["chunks"]) > 1
    immediate = (
        await post(
            account,
            "/digest",
            {"event_type": "unknown.type", "provider_id": "core.smtp", "count": 5},
        )
    ).json()
    assert immediate["strategy"] == "immediate" and immediate["chunks"] == []
    missing = (
        await post(account, "/digest", {"event_type": "a.b", "provider_id": "ghost", "count": 5})
    ).json()
    assert missing["code"] == "provider_unavailable"


# Provider health -----------------------------------------------------------------------------


async def test_overview_reports_capabilities_and_health_without_secrets(account):
    body = (await get(account, "/overview")).json()
    by_id = {p["provider_id"]: p for p in body["providers"]}
    assert by_id["core.smtp"]["health"]["status"] in {"ok", "not_configured"}
    assert by_id["core.smtp"]["health"]["probe"] == "configuration"
    assert by_id["core.smtp"]["profile"]["capabilities"]["links"] is True
    assert body["retry_policy"]["backoff_seconds"] == [60, 120]
    text = json.dumps(body).lower()
    for forbidden in ("password", "private_key", "secret", "token"):
        assert forbidden not in text


# Test sends -----------------------------------------------------------------------------------


async def send(account, destination, **extra):
    return await post(
        account, "/test-send", {"destination_id": str(destination), "confirm": True, **extra}
    )


async def destination_of(account):
    async with SessionLocal() as db:
        return await db.scalar(
            select(NotificationDestination).where(NotificationDestination.user_id == account)
        )


async def test_test_send_requires_confirmation_and_owns_the_destination(account, transport, other):
    destination = await destination_of(account)
    before = await counts()
    unconfirmed = await post(account, "/test-send", {"destination_id": str(destination.id)})
    assert unconfirmed.status_code == 422
    falsy = await post(
        account, "/test-send", {"destination_id": str(destination.id), "confirm": False}
    )
    assert falsy.status_code == 422
    assert await counts() == before
    await make_admin(other)
    async with SessionLocal() as db:
        async with client(db, other) as http:
            foreign = await http.post(
                f"{BASE}/test-send", json={"destination_id": str(destination.id), "confirm": True}
            )
    assert foreign.status_code == 404


async def test_test_send_uses_the_real_dispatcher_and_is_labelled(account, transport):
    destination = await destination_of(account)
    response = await send(account, destination.id, variant="rich", batch_size=3)
    assert response.status_code == 202
    body = response.json()
    assert body["labelled"] == "test" and body["queued"] == 3 and body["variant"] == "rich"
    async with SessionLocal() as db:
        for _ in range(5):
            await process_pending_deliveries(db)
    assert len(transport.messages) == 3
    assert {m.purpose for m in transport.messages} == {"test"}
    assert all("Test" in m.title for m in transport.messages)
    async with SessionLocal() as db:
        rows = list(await db.scalars(select(Notification).where(Notification.user_id == account)))
    assert {n.purpose for n in rows} == {"test"} and all(not n.inbox_visible for n in rows)
    assert all(n.media_id == destination.id for n in rows)


async def test_test_send_batch_is_capped_and_variant_validated(account, transport):
    destination = await destination_of(account)
    assert (await send(account, destination.id, batch_size=6)).status_code == 422
    assert (await send(account, destination.id, variant="bogus")).status_code == 422


# Deliveries, retries, redaction ---------------------------------------------------------------


async def failed_delivery(
    account, *, purpose="test", error="token=abcd1234efgh5678 hunter2", attempts=1
):
    destination = await destination_of(account)
    now = int(time.time())
    async with SessionLocal() as db:
        notice = Notification(
            user_id=account,
            kind="provider_test",
            event_type="notification.provider.test",
            purpose=purpose,
            media_type="notification_destination",
            media_id=destination.id,
            title="Private title for jane@example.com",
            body="Your code is 482913",
            event_at=now,
            dedupe_key=f"t:{uuid4()}",
            expires_at=now + 600,
        )
        db.add(notice)
        await db.flush()
        delivery = NotificationDelivery(
            notification_id=notice.id,
            provider_id=destination.provider_id,
            destination_id=destination.id,
            destination_revision=1,
            status="failed_permanent",
            attempts=attempts,
            last_error=error,
            attempted_at=now - 3600,
            fallback="images_dropped",
        )
        db.add(delivery)
        await db.flush()
        db.add(
            NotificationDeliveryAttempt(
                delivery_id=delivery.id,
                number=1,
                claim_token=uuid4(),
                started_at=now - 3600,
                finished_at=now - 3599,
                outcome="failed",
                error_code="provider_error",
            )
        )
        await db.commit()
        return delivery.id


async def test_delivery_history_gives_safe_reasons_and_never_content(account, transport):
    delivery_id = await failed_delivery(account)
    body = (await get(account, "/deliveries", admin=False)).json()
    item = next(d for d in body["deliveries"] if d["id"] == str(delivery_id))
    assert item["reason"]["code"] == "unrecognized" and item["reason"]["action"]
    assert item["attempt_log"][0]["reason"]["code"] == "provider_error"
    assert item["fallbacks"][0]["code"] == "unrecognized" or item["fallbacks"][0]["label"]
    text = json.dumps(body)
    for leaked in ("hunter2", "abcd1234", "jane@example.com", "482913", "Private title"):
        assert leaked not in text


async def test_history_and_retry_are_isolated_between_accounts(account, transport, other):
    delivery_id = await failed_delivery(account)
    assert (await get(other, "/deliveries", admin=False)).json()["deliveries"] == []
    async with SessionLocal() as db:
        async with client(db, other) as http:
            assert (await http.post(f"{BASE}/deliveries/{delivery_id}/retry")).status_code == 404


async def test_owner_retry_requeues_through_the_dispatcher_with_a_cooldown(account, transport):
    delivery_id = await failed_delivery(account)
    async with SessionLocal() as db:
        async with client(db, account) as http:
            first = await http.post(f"{BASE}/deliveries/{delivery_id}/retry")
            assert first.status_code == 200 and first.json()["status"] == "pending"
            assert (await http.post(f"{BASE}/deliveries/{delivery_id}/retry")).status_code == 409
    async with SessionLocal() as db:
        await process_pending_deliveries(db)
        row = await db.get(NotificationDelivery, delivery_id)
        await db.refresh(row)
        assert row.status == "sent" and len(transport.messages) == 1


async def test_exhausted_deliveries_cannot_exceed_their_attempt_budget(account, transport):
    delivery_id = await failed_delivery(account, attempts=MAX_ATTEMPTS)
    async with SessionLocal() as db:
        async with client(db, account) as http:
            response = await http.post(f"{BASE}/deliveries/{delivery_id}/retry")
    assert response.status_code == 409 and "new test" in response.json()["detail"]
    listed = (await get(account, "/deliveries", admin=False)).json()["deliveries"]
    assert listed[0]["retry_allowed"] is False


async def test_retry_is_refused_for_sensitive_deliveries(account, transport):
    delivery_id = await failed_delivery(account, purpose="recovery")
    async with SessionLocal() as db:
        async with client(db, account) as http:
            response = await http.post(f"{BASE}/deliveries/{delivery_id}/retry")
    assert response.status_code == 409
    listed = (await get(account, "/deliveries", admin=False)).json()["deliveries"]
    assert listed[0]["retry_allowed"] is False


async def test_recent_failure_retry_hits_the_cooldown(account, transport):
    delivery_id = await failed_delivery(account)
    async with SessionLocal() as db:
        await db.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .values(attempted_at=int(time.time()))
        )
        await db.commit()
        async with client(db, account) as http:
            assert (await http.post(f"{BASE}/deliveries/{delivery_id}/retry")).status_code == 429


# Redaction fixtures ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        ("login code 482913 for you", "482913"),
        ("mail jane.doe@example.com now", "jane.doe"),
        ("Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ("password=hunter2 failed", "hunter2"),
        ("https://hooks.example.com/api/webhooks/123456/AbCdEfGhIjKlMnOpQrStUvWx", "AbCdEf"),
        ("https://x.example/verify?token=abc123&u=me", "abc123"),
        ("key vendorkey_0123456789abcdefghijklmnop", "0123456789abcdef"),
    ],
)
def test_redaction_removes_credentials_codes_recipients_and_link_tokens(raw, gone):
    assert gone not in redact(raw)


def test_unknown_reason_text_is_never_echoed_and_known_codes_are_actionable():
    assert reason("password=hunter2")["code"] == "unrecognized"
    assert reason(None)["code"] == "unrecognized"
    for code in ("rate_limited", "invalid_config", "attempts_exhausted", "provider_unavailable"):
        assert reason(code)["code"] == code and reason(code)["action"]


def test_failure_machine_matches_the_dispatcher_constants():
    outcome = diagnostics.failure_attempts("rate_limited", start=0)
    assert len(outcome["attempts"]) == MAX_ATTEMPTS
    assert [a.get("next_attempt_at") for a in outcome["attempts"]] == [60, 180, None]
