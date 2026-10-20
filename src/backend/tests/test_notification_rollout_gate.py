"""Final rollout gate: one integrated path from event to history over the real transports.

Child issues own the focused tests; these scenarios prove the pieces still compose after the
whole overhaul is integrated: event -> story -> policy -> aggregate -> render -> deliver ->
history, with replay, restart recovery, partial failure, revocation, retention and the
compatibility adapters in the same database.
"""

import re
import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update

from src.core.config import settings
from src.core.preferences import save_preferences
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.notification_story import NotificationStory, NotificationStoryRevision
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.session import SessionLocal
from src.features.notification_inbox import retention_policy
from src.features.notification_providers import smtp as smtp_module
from src.features.notification_providers.base import DeliveryResult
from src.features.notification_providers.delivery import process_pending_deliveries
from tests import test_notification_email as email_tests
from tests.test_notification_email import release_windows, verify  # noqa: F401
from tests.test_notification_smtp_rich import (  # noqa: F401
    deliver_all,
    parts,
    public_origin,
    subscribe,
)
from tests.test_notification_story_pages import (  # noqa: F401
    PLUGIN,
    SECRET_FACT,
    notification_world,
    rich,
)

mailbox = email_tests.mailbox


async def deliveries(owner, notification_id=None):
    async with SessionLocal() as db:
        query = (
            select(NotificationDelivery)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(Notification.user_id == owner)
        )
        if notification_id:
            query = query.where(NotificationDelivery.notification_id == notification_id)
        return list((await db.scalars(query)).all())


async def test_event_to_history_with_replay_partial_failure_and_retention(
    world, mailbox, monkeypatch
):
    await subscribe(world, mailbox, "good@example.test")
    await subscribe(world, mailbox, "bad@example.test")
    real_deliver = smtp_module.SmtpNotificationProvider.deliver

    async def flaky(self, db, destination, message):
        if destination.address == "bad@example.test":
            return DeliveryResult(False, retryable=True, error="provider_unavailable")
        return await real_deliver(self, db, destination, message)

    monkeypatch.setattr(smtp_module.SmtpNotificationProvider, "deliver", flaky)

    # event -> story: one accepted event creates exactly one story and one inbox item.
    notice, story = await world.emit(rich(title="Build 1"), key="gate", dedupe="gate-1")
    # Replay of the same producer key is a receipt hit, not a second item or story.
    replay, replay_story = await world.emit(rich(title="Build 1"), key="gate", dedupe="gate-1")
    assert replay is None and replay_story == story
    async with SessionLocal() as db:
        receipts = (
            await db.scalars(
                select(NotificationReceipt).where(NotificationReceipt.dedupe_key == "gate-1")
            )
        ).all()
        assert len(receipts) == 1
        assert len((await db.scalars(world.stories())).all()) == 1

    # policy -> aggregate -> render -> deliver: the healthy destination gets rich mail even
    # though the other destination fails, and the failure keeps its own retry state.
    assert await deliver_all() == 1
    assert [mail["To"] for mail in mailbox] == ["good@example.test"]
    plain, html = parts(mailbox[0])
    assert "Build 1" in plain and SECRET_FACT in html
    rows = {row.status for row in await deliveries(world.owner, notice)}
    assert rows == {"sent", "retry_wait"}
    failed = [row for row in await deliveries(world.owner) if row.status == "retry_wait"]
    assert [row.last_error for row in failed] == ["provider_error"]  # a code, never provider text

    # Restart recovery: a worker that died holding a lease leaves the delivery reclaimable and
    # the healthy mail is not sent twice.
    async with SessionLocal() as db:
        await db.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.status == "retry_wait")
            .values(status="processing", lease_until=int(time.time()) - 1, claim_token=None)
        )
        await db.commit()
    mailbox.clear()
    await deliver_all()
    assert [mail["To"] for mail in mailbox] == []  # still failing, never duplicated to good

    # Once the failed destination recovers, only its outstanding work is retried.
    monkeypatch.setattr(smtp_module.SmtpNotificationProvider, "deliver", real_deliver)
    async with SessionLocal() as db:
        await db.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.id == failed[0].id)
            .values(next_attempt_at=0)
        )
        await db.commit()
    assert await deliver_all() == 1
    assert [mail["To"] for mail in mailbox] == ["bad@example.test"]
    assert {row.status for row in await deliveries(world.owner, notice)} == {"sent"}

    # story -> history: a later revision updates the same story and keeps the earlier one.
    revised, same_story = await world.emit(
        rich(title="Build 2"), key="gate", phase="available", dedupe="gate-2"
    )
    assert same_story == story
    async with SessionLocal() as db:
        revisions = (
            await db.scalars(
                select(NotificationStoryRevision)
                .where(NotificationStoryRevision.story_id == story)
                .order_by(NotificationStoryRevision.revision)
            )
        ).all()
        assert [r.revision for r in revisions] == [1, 2]
    page = await world.client.get(f"/api/notifications/stories/{story}")
    assert page.status_code == 200 and page.json()["title"] == "Build 2"

    # Inbox read state and visible expiry use the existing retention semantics.
    listing = (await world.client.get("/api/notifications")).json()
    assert listing["items"] and all(item["read_at"] is None for item in listing["items"])
    item_id = listing["items"][0]["id"]
    assert (await world.client.post(f"/api/notifications/{item_id}/read")).status_code == 204
    policy = (await world.client.get("/api/notifications/policy")).json()
    assert policy == retention_policy({})  # inherited server default, no parallel mechanism
    async with SessionLocal() as db:
        await db.execute(
            update(Notification)
            .where(Notification.id == UUID(item_id))
            .values(expires_at=int(time.time()) - 5, inbox_visible=False)
        )
        await db.commit()
    expired = (await world.client.get("/api/notifications?state=expired")).json()
    assert item_id in {item["id"] for item in expired["items"]}
    # Expired items stay inspectable by their story link for the owner within retention.
    assert (await world.client.get(f"/api/notifications/stories/{story}")).status_code == 200
    assert revised


async def test_revoked_grant_suppresses_queued_work_but_keeps_history(world, mailbox):
    await subscribe(world, mailbox)
    notice, story = await world.emit(rich(title="Before revoke"), key="revoke")
    async with SessionLocal() as db:
        grants = (
            await db.scalars(
                select(PluginPermissionGrant).where(PluginPermissionGrant.plugin_id == PLUGIN)
            )
        ).all()
        for grant in grants:
            await db.delete(grant)
        await db.commit()
    await deliver_all()
    assert mailbox == []  # authority is rechecked immediately before every physical send
    async with SessionLocal() as db:
        assert await db.get(Notification, notice) is not None
        assert await db.get(NotificationStory, story) is not None
        stored = await db.scalar(
            select(NotificationStoryRevision).where(NotificationStoryRevision.story_id == story)
        )
        assert stored.content["title"] == "Before revoke"
    outcomes = {(row.status, row.last_error) for row in await deliveries(world.owner, notice)}
    assert ("suppressed", "source_revoked") in outcomes


async def test_digest_boundaries_hold_through_the_integrated_path(world, mailbox, monkeypatch):
    """Real SMTP stays silent until the boundary and sends each window exactly once."""
    await subscribe(world, mailbox)
    now = int(datetime(2030, 1, 15, 7, 59, tzinfo=UTC).timestamp())
    clock = [now]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    async with SessionLocal() as db:
        await save_preferences(
            db,
            world.owner,
            {
                "notification_timing": {
                    "timezone": "UTC",
                    "digest_times": ["08:00"],
                    "rules": [{"category": "plugin", "mode": "digest"}],
                }
            },
        )
    for number in range(2):
        await world.emit(rich(title=f"Item {number}"), key=f"digest-{number}")
    async with SessionLocal() as db:
        assert await process_pending_deliveries(db) == 0
    clock[0] = now + 59
    async with SessionLocal() as db:
        assert await process_pending_deliveries(db) == 0
    assert mailbox == []

    clock[0] = now + 60  # exactly 08:00; this new item belongs to tomorrow's window
    await world.emit(rich(title="Item 2"), key="digest-2")
    async with SessionLocal() as db:
        assert await process_pending_deliveries(db) == 1
    assert len(mailbox) == 1
    first = " ".join(parts(mailbox[0]))
    assert "Item 0" in first and "Item 1" in first and "Item 2" not in first
    assert re.search(r"/notifications/stories/[0-9a-f-]{36}", first)

    clock[0] += 86400 - 1
    async with SessionLocal() as db:
        assert await process_pending_deliveries(db) == 0
    clock[0] += 1
    async with SessionLocal() as db:
        assert await process_pending_deliveries(db) == 1
        assert await process_pending_deliveries(db) == 0
    assert len(mailbox) == 2 and "Item 2" in " ".join(parts(mailbox[1]))
    assert {row.status for row in await deliveries(world.owner)} == {"sent"}


async def test_quiet_hours_release_real_mail_at_the_boundary_without_delaying_inbox(
    world, mailbox, monkeypatch
):
    await subscribe(world, mailbox)
    clock = [int(datetime(2030, 1, 15, 23, tzinfo=UTC).timestamp())]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    async with SessionLocal() as db:
        await save_preferences(
            db,
            world.owner,
            {
                "notification_timing": {
                    "timezone": "UTC",
                    "quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"},
                    "rules": [{"category": "plugin", "mode": "immediate"}],
                }
            },
        )
    notice, story = await world.emit(rich(title="Quiet-hours item"), key="quiet")
    listing = (await world.client.get("/api/notifications")).json()
    assert str(notice) in {item["id"] for item in listing["items"]}
    for stamp in (clock[0], clock[0] + 8 * 3600 - 1):
        clock[0] = stamp
        async with SessionLocal() as db:
            assert await process_pending_deliveries(db) == 0
        assert mailbox == []
    clock[0] += 1
    async with SessionLocal() as db:
        assert await process_pending_deliveries(db) == 1
        assert await process_pending_deliveries(db) == 0
    assert len(mailbox) == 1 and "Quiet-hours item" in " ".join(parts(mailbox[0]))
    assert (await world.client.get(f"/api/notifications/stories/{story}")).status_code == 200


def test_server_retention_default_is_the_only_retention_mechanism():
    policy = retention_policy({"notification_retention_inherit": True})
    assert (
        policy["effective_days"] == settings.NOTIFICATION_RETENTION_DEFAULT_DAYS
        or (policy["limited"])
    )
    capped = retention_policy(
        {"notification_retention_inherit": False, "notification_retention_days": 365}
    )
    if settings.NOTIFICATION_RETENTION_MAXIMUM_DAYS:
        assert capped["effective_days"] <= settings.NOTIFICATION_RETENTION_MAXIMUM_DAYS
    assert capped["requested_days"] == 365  # the person's request is never rewritten
