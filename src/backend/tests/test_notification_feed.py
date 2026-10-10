"""Real PostgreSQL replay, transaction ordering and explicit plugin scope boundaries."""

import asyncio
import json
import time
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select, update

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_audit import (
    NotificationLifecycleAudit,
    NotificationLifecycleOutbox,
)
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.session import SessionLocal
from src.features.notification_audit import change_notices, publish_lifecycle, record_deliveries
from src.features.notification_controller import emit_legacy_rows
from src.features.notification_destinations import withdraw_provider_work
from src.features.notification_feed import poll_lifecycle
from src.features.notification_lifecycle import delete_notice, dismiss
from src.plugin_api.capabilities import capability_implies
from src.plugin_api.gateway import dispatch_gateway_request
from src.plugin_api.notification_contracts import (
    NotificationLifecyclePage,
    NotificationLifecycleQuery,
)
from tests.test_notification_core import endpoint
from tests.test_notification_sources import call, definition, event
from tests.test_notification_sources import source as source


def metadata(source, **changes):
    owner, plugin, installation = source
    return NotificationLifecycleOutbox(
        user_id=owner,
        plugin_id=plugin,
        installation_id=installation,
        notification_id=uuid4(),
        status="created",
        **changes,
    )


async def page(db, source, **query):
    owner, plugin, installation = source
    result = await poll_lifecycle(db, plugin, installation, owner, query)
    NotificationLifecyclePage.model_validate(result)
    await db.commit()
    return result


async def accepted(db, source):
    await call(db, source, "notification_sources.register", definition(source[1]))
    result = await call(db, source, "notifications.emit", event(source[1]))
    return UUID(result["notification_id"])


async def test_actual_inbox_lifecycle_survives_notice_deletion_without_content(source):
    async with SessionLocal() as db:
        identity = await accepted(db, source)
        await change_notices(
            db, update(Notification).where(Notification.id == identity).values(read_at=1), "read"
        )
        await change_notices(
            db,
            update(Notification).where(Notification.id == identity).values(read_at=None),
            "unread",
        )
        assert await dismiss(db, source[0], identity)
        assert await delete_notice(db, source[0], identity)
        await db.execute(delete(Notification).where(Notification.id == identity))
        await db.commit()
        await publish_lifecycle(db)
        result = await page(db, source)
        assert [item["status"] for item in result["events"]] == [
            "created",
            "read",
            "unread",
            "dismissed",
            "deleted",
        ]
        for item in result["events"]:
            assert set(item) == {"id", "notification_id", "delivery_id", "status", "occurred_at"}
            assert item["notification_id"] == str(identity)
            assert item["delivery_id"] is None
        assert "Release: Example" not in json.dumps(result)
        assert "source" not in json.dumps(result)


async def test_late_producer_commit_cannot_fall_behind_an_already_returned_cursor(source):
    async with SessionLocal() as early, SessionLocal() as later, SessionLocal() as publisher:
        first = metadata(source)
        early.add(first)
        await early.flush()
        second = metadata(source)
        later.add(second)
        await later.commit()
        assert await publish_lifecycle(publisher) == 1
        initial = await page(publisher, source)
        assert [item["id"] for item in initial["events"]] == [str(second.id)]
        await early.commit()
        assert await publish_lifecycle(publisher) == 1
        follow = await page(publisher, source, cursor=initial["cursor"])
        assert [item["id"] for item in follow["events"]] == [str(first.id)]
        assert not follow["resync_required"]


async def test_capture_rolls_back_with_notification_and_no_false_audit_is_published(source):
    async with SessionLocal() as db:
        await emit_legacy_rows(
            db,
            source[0],
            [
                {
                    "kind": "plugin",
                    "media_type": "plugin",
                    "media_id": uuid4(),
                    "event_at": int(time.time()),
                    "dedupe_key": "rollback-test",
                    "title": "Private source text",
                    "body": "Never committed",
                }
            ],
            source=source[1],
            source_installation_id=source[2],
        )
        await db.rollback()
        assert (
            await db.scalar(
                select(NotificationLifecycleOutbox.id).where(
                    NotificationLifecycleOutbox.user_id == source[0]
                )
            )
            is None
        )
        await publish_lifecycle(db)
        assert (await page(db, source))["events"] == []


async def test_competing_publishers_preserve_each_event_once_and_page_order(source):
    async with SessionLocal() as db:
        rows = [metadata(source) for _ in range(41)]
        db.add_all(rows)
        await db.commit()

    async def publish():
        async with SessionLocal() as db:
            return await publish_lifecycle(db, limit=20)

    assert sorted(await asyncio.gather(publish(), publish())) == [20, 20]
    await publish()
    async with SessionLocal() as db:
        cursor = None
        seen = []
        while True:
            response = await page(db, source, cursor=cursor, limit=7)
            seen.extend(item["id"] for item in response["events"])
            cursor = response["cursor"]
            if not response["has_more"]:
                break
        assert seen == [str(row.id) for row in rows]
        assert len(set(seen)) == 41
        assert (await page(db, source, cursor=cursor))["events"] == []


async def test_scope_cursor_tamper_and_installation_replacement(source):
    async with SessionLocal() as db:
        db.add_all(
            [
                metadata(source),
                metadata((source[0], "other.plugin", source[2])),
                metadata((source[0], source[1], uuid4())),
            ]
        )
        await db.commit()
        await publish_lifecycle(db)
        own = await page(db, source)
        assert len(own["events"]) == 1
        for owner, installation, cursor in (
            (uuid4(), source[2], own["cursor"]),
            (source[0], uuid4(), own["cursor"]),
            (source[0], source[2], "forged"),
            (source[0], source[2], "x" + own["cursor"][1:]),
        ):
            with pytest.raises(ValueError, match="cursor"):
                await poll_lifecycle(db, source[1], installation, owner, {"cursor": cursor})
            await db.rollback()


async def test_delivery_states_are_visible_only_to_actual_provider_installation(source):
    owner, plugin, installation = source
    provider = f"{plugin}.webhook"
    async with SessionLocal() as db:
        identity = await accepted(db, source)
        target = endpoint(owner, provider_id=provider, installation_id=installation)
        db.add(target)
        db.add(
            PluginNotificationProviderRegistration(
                plugin_id=plugin,
                installation_id=installation,
                provider_id=provider,
                name="Own provider",
                action_id="render",
                transport="discord_webhook",
            )
        )
        await db.flush()
        delivery = NotificationDelivery(
            notification_id=identity,
            destination_id=target.id,
            provider_id=provider,
            destination_revision=1,
            projection="public",
            status="pending",
        )
        db.add(delivery)
        await db.flush()
        await record_deliveries(db, [delivery.id])
        await withdraw_provider_work(db, provider)
        await db.commit()
        await publish_lifecycle(db)
        result = await page(db, source)
        deliveries = [item for item in result["events"] if item["delivery_id"]]
        assert [item["status"] for item in deliveries] == ["pending", "suppressed"]
        assert all(item["delivery_id"] == str(delivery.id) for item in deliveries)
        assert str(target.id) not in json.dumps(result)
        assert "provider_revoked" not in json.dumps(result)
        assert len(result["events"]) == 3  # no built-in inbox delivery disclosure
        await db.execute(
            delete(PluginNotificationProviderRegistration).where(
                PluginNotificationProviderRegistration.provider_id == provider
            )
        )
        await db.commit()
        assert len((await page(db, source))["events"]) == 3


async def test_retention_expired_cursor_requires_explicit_resynchronisation(source, monkeypatch):
    monkeypatch.setattr(settings, "NOTIFICATION_AUDIT_RETENTION_DAYS", 90)
    async with SessionLocal() as db:
        before = await page(db, source)
        db.add(metadata(source))
        await db.commit()
        await publish_lifecycle(db)
        await db.execute(
            update(NotificationLifecycleAudit).values(published_at=int(time.time()) - 91 * 86400)
        )
        await db.commit()
        await publish_lifecycle(db)
        expired = await page(db, source, cursor=before["cursor"])
        assert expired["resync_required"]
        assert expired["events"] == []
        restarted = await page(db, source, cursor=expired["cursor"])
        assert not restarted["resync_required"]
        assert restarted["events"] == []


async def test_gateway_requires_separate_live_grant_and_revocation_stops_reads(source):
    owner, plugin, installation = source
    args = dict(
        plugin_id=plugin,
        installation_id=installation,
        user_id=owner,
        method="notifications.lifecycle.poll",
        payload={},
    )
    async with SessionLocal() as db:
        for capability in ("notifications", "events.subscribe", "api.full", "notifications.emit"):
            assert not capability_implies(capability, "notifications.lifecycle.read")
            with pytest.raises(PermissionError):
                await dispatch_gateway_request(db, capability=capability, **args)
        grant = PluginPermissionGrant(
            plugin_id=plugin,
            installation_id=installation,
            user_id=owner,
            capability="notifications.lifecycle.read",
            capability_version=1,
        )
        db.add(grant)
        await db.commit()
        result = await dispatch_gateway_request(
            db, capability="notifications.lifecycle.read", **args
        )
        assert result["events"] == []
        grant.revoked_at = int(time.time())
        await db.commit()
        with pytest.raises(PermissionError):
            await dispatch_gateway_request(db, capability="notifications.lifecycle.read", **args)


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": str(uuid4())},
        {"installation_id": str(uuid4())},
        {"limit": True},
        {"limit": 0},
        {"limit": 201},
        {"limit": "10"},
        {"cursor": ""},
        {"cursor": "x" * 1025},
    ],
)
def test_feed_cannot_choose_scope_or_unbounded_pages(payload):
    with pytest.raises(ValidationError):
        NotificationLifecycleQuery.model_validate(payload)
