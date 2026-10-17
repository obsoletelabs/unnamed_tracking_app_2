# ruff: noqa: F401, F811 - fixtures are shared with test_notification_browser
"""Story-aware browser push: stable tags, replacement, safe previews, and fail-closed display."""

import json
import time
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import http_ece
import pytest
from sqlalchemy import select, update

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_remote_reference import NotificationRemoteReference
from src.database.session import SessionLocal
from src.features import notification_browser_preview as preview
from src.features.notification_contract import BUILTIN_PROFILES
from src.features.notification_providers import browser
from src.features.notification_providers import delivery as dispatcher
from src.features.notification_providers.capabilities import resolve_profile
from src.plugin_api.notification_story_contracts import NotificationContent
from tests.test_notification_browser import (
    api,
    enroll,
    push_account,
    push_http,
)
from tests.test_notification_dispatch_capabilities import accept, drain, row

STATUS = "/api/settings/notification-providers/browser-destinations/{}/status"


@pytest.fixture(autouse=True)
def no_windows(monkeypatch):
    monkeypatch.setattr(dispatcher, "assign_window", AsyncMock(return_value=None))


def decode(account, captured):
    return json.loads(
        http_ece.decrypt(
            captured["body"],
            private_key=account.private,
            auth_secret=b"owned-test-token",
            version="aes128gcm",
        )
    )


async def ask(client, endpoint_id, notice_id, revision=1):
    response = await client.get(
        STATUS.format(endpoint_id), params={"revision": revision, "notification": str(notice_id)}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def setup(push_account, *rows):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
    ids = await accept(push_account.user_id, *rows)
    return endpoint_id, ids


def test_browser_profile_replaces_in_place_and_stays_generic_by_default():
    profile = resolve_profile(browser.BrowserNotificationProvider())
    assert profile.can_edit and profile.capabilities.message_updates == "replace"
    assert BUILTIN_PROFILES["core.browser"].generic_preview_only
    assert BUILTIN_PROFILES["core.browser"].capabilities.max_payload_bytes == 4096
    assert settings.NOTIFICATION_BROWSER_PREVIEWS == "generic"


async def test_push_carries_only_story_identities_and_a_replacing_topic(push_account, push_http):
    endpoint_id, [notice_id] = await setup(push_account, row("available", title="Secret title"))
    assert await drain() >= 1
    captured = push_http.captured[0]
    envelope = decode(push_account, captured)
    async with SessionLocal() as db:
        notice = await db.get(Notification, notice_id)
        assert envelope == {
            "version": 1,
            "destination": str(endpoint_id),
            "revision": 1,
            "generation": push_account.context.generation,
            "notification": str(notice_id),
            "operation": "send",
            "story": str(notice.story_id),
            "story_revision": 1,
        }
        assert len(json.dumps(envelope)) < 512
        assert (
            preview.push_topic(endpoint_id, preview.story_identity(notice))
            == (captured["headers"]["Topic"])
        )
        assert len(captured["headers"]["Topic"]) == 32
        saved = await db.scalar(select(NotificationRemoteReference))
        assert saved.remote_ref == f"story:{notice.story_id}"
    assert b"Secret" not in json.dumps(envelope).encode()


async def test_a_later_revision_replaces_in_place_and_a_replay_adds_no_alert(
    push_account, push_http
):
    _, [notice_id] = await setup(push_account, row("available"))
    await drain()
    assert await accept(push_account.user_id, row("failed", title="Update failed")) == [notice_id]
    await drain()
    first, second = (decode(push_account, item) for item in push_http.captured[:2])
    assert (first["operation"], second["operation"]) == ("send", "edit")
    assert first["story"] == second["story"] and second["story_revision"] == 2
    assert push_http.captured[0]["headers"]["Topic"] == push_http.captured[1]["headers"]["Topic"]
    # Identical content and a replayed event produce no further native alert.
    await accept(push_account.user_id, row("failed", title="Update failed"))
    await drain()
    assert len(push_http.captured) == 2


async def test_without_a_device_reference_a_revision_follows_the_configured_policy(
    push_account, push_http
):
    _, [notice_id] = await setup(push_account, row("available"))
    await drain()
    async with SessionLocal() as db:
        await db.execute(
            update(NotificationRemoteReference)
            .where(NotificationRemoteReference.notification_id == notice_id)
            .values(remote_ref=None)
        )
        await db.commit()
    await accept(push_account.user_id, row("failed", title="Update failed"))
    await drain()
    assert [decode(push_account, item)["operation"] for item in push_http.captured] == [
        "send",
        "follow_up",
    ]


async def test_default_preview_is_constant_and_still_story_addressed(push_account, push_http):
    endpoint_id, [notice_id] = await setup(push_account, row("available", title="Private"))
    await drain()
    async with SessionLocal() as db, api(db, push_account) as client:
        state = await ask(client, endpoint_id, notice_id)
        story_id = (await db.get(Notification, notice_id)).story_id
    shown = state["preview"]
    assert shown["generic"] is True
    assert shown["title"] == preview.GENERIC_TITLE and shown["body"] == preview.GENERIC_BODY
    assert shown["url"] == f"/notifications/stories/{story_id}"
    assert "Private" not in json.dumps(state) and "image" not in shown and "actions" not in shown


async def test_story_mode_shows_safe_text_but_stays_generic_for_private_contexts(
    push_account, push_http, monkeypatch
):
    monkeypatch.setattr(settings, "NOTIFICATION_BROWSER_PREVIEWS", "story")
    endpoint_id, [notice_id] = await setup(
        push_account, row("available", title="Episode‮ 5\n" + "x" * 200, body="Now streaming")
    )
    await drain()
    async with SessionLocal() as db, api(db, push_account) as client:
        shown = (await ask(client, endpoint_id, notice_id))["preview"]
        assert shown["generic"] is False and shown["body"] == "Now streaming"
        assert len(shown["title"]) <= preview.MAX_TITLE and "‮" not in shown["title"]
        assert "\n" not in shown["title"] and shown["actions"] == [] and shown["image"] is None
        # A non-canonical projection or non-standard purpose never reveals item text.
        await db.execute(update(NotificationDelivery).values(projection="public"))
        await db.commit()
        assert (await ask(client, endpoint_id, notice_id))["preview"]["generic"] is True
        await db.execute(update(NotificationDelivery).values(projection="canonical"))
        await db.execute(update(Notification).values(purpose="verification"))
        await db.commit()
        assert (await ask(client, endpoint_id, notice_id))["preview"]["generic"] is True


async def test_credential_shaped_text_is_withheld_from_the_device(
    push_account, push_http, monkeypatch
):
    monkeypatch.setattr(settings, "NOTIFICATION_BROWSER_PREVIEWS", "story")
    endpoint_id, [notice_id] = await setup(push_account, row("available"))
    await drain()
    async with SessionLocal() as db, api(db, push_account) as client:
        await db.execute(update(Notification).values(body="password: hunter2hunter2"))
        await db.commit()
        assert (await ask(client, endpoint_id, notice_id))["preview"]["generic"] is True


async def test_gone_expired_foreign_or_unrouted_items_have_no_preview(push_account, push_http):
    endpoint_id, [notice_id] = await setup(push_account, row("available"))
    await drain()
    async with SessionLocal() as db, api(db, push_account) as client:
        assert (await ask(client, endpoint_id, notice_id))["preview"] is not None
        assert (await ask(client, endpoint_id, uuid4()))["preview"] is None
        await db.execute(update(Notification).values(expires_at=int(time.time()) - 5))
        await db.commit()
        assert (await ask(client, endpoint_id, notice_id))["preview"] is None
        await db.execute(update(Notification).values(expires_at=None, deleted_at=int(time.time())))
        await db.commit()
        assert (await ask(client, endpoint_id, notice_id))["preview"] is None
        await db.execute(update(Notification).values(deleted_at=None))
        await db.execute(update(NotificationDelivery).values(status="suppressed"))
        await db.commit()
        assert (await ask(client, endpoint_id, notice_id))["preview"] is None
        await db.execute(update(NotificationDelivery).values(status="sent"))
        await db.commit()
        assert (await ask(client, endpoint_id, notice_id))["preview"] is not None


def test_application_paths_reject_other_origins_and_traversal():
    for good in ("/tv/1", "/notifications/stories/x?a=1"):
        assert preview._app_path(good) == good
    for bad in ("https://evil.example/", "//evil.example", "/a/../b", "/a\\b", "tv/1", "/a b", ""):
        assert preview._app_path(bad) is None


async def test_status_without_a_notification_is_unchanged(push_account, push_http):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        endpoint = await db.get(NotificationDestination, endpoint_id)
        response = await client.get(STATUS.format(endpoint_id), params={"revision": 1})
        assert response.json() == {"enabled": True, "generation": push_account.context.generation}
        assert endpoint.revision == 1
        other = await client.get(STATUS.format(UUID(int=1)), params={"revision": 1})
        assert other.json()["enabled"] is False


def content(**changes):
    document = {
        "title": "Release",
        "summary": "Out now",
        "images": [
            {"source": {"kind": "external", "url": "https://img.example.com/a.png"}, "alt": "Cover"}
        ],
        "actions": [
            {"id": "a", "label": "Library", "target": {"kind": "host_route", "path": "/library"}},
            {"id": "b", "label": "Docs", "target": {"kind": "link", "url": "https://example.com"}},
            {
                "id": "c",
                "label": "Run",
                "target": {"kind": "plugin_action", "action_id": "x.y", "input": {}},
            },
            {"id": "d", "label": "Shows", "target": {"kind": "host_route", "path": "/tv"}},
            {"id": "e", "label": "Games", "target": {"kind": "host_route", "path": "/games"}},
        ],
    }
    document.update(changes)
    return NotificationContent.model_validate(document)


def test_only_same_origin_routes_become_actions_and_images_follow_policy(monkeypatch):
    item = content()
    # Links and plugin actions never leave the signed-in app, and the count is capped.
    assert preview._actions(item) == (
        ("a", "Library", "/library"),
        ("d", "Shows", "/tv"),
    )
    for mode, expected in (
        ("external", "https://img.example.com/a.png"),
        ("proxied", None),
        ("off", None),
    ):
        monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MODE", mode)
        assert preview._image(item) == expected
    assert preview._image(None) is None and preview._actions(None) == ()
