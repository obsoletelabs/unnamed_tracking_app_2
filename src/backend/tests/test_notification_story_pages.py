"""Story pages, live links, public projection and reauthorised plugin actions."""

import time
from html.parser import HTMLParser
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select, update

from src.core.auth import get_current_user
from src.database.models.notification import Notification
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.notification_story import (
    NotificationLiveLink,
    NotificationStory,
    NotificationStoryRevision,
)
from src.database.models.plugin_notification_type import PluginNotificationTypeRegistration
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import notification_story_pages as pages
from src.features.notification_controller import (
    NotificationDraft,
    NotificationInterpretation,
    emit_interpreted,
)
from src.features.notification_lifecycle import delete_notice
from src.features.notification_policy import Trust
from src.features.notification_safety import UnsafeContentError
from src.features.notification_stories import StoryReference
from src.main import app
from src.plugin_api.notification_story_contracts import (
    NOTIFICATION_CONTRACT_VERSION,
    NotificationContent,
    StoryLifecycleDeclaration,
    StoryPhase,
    StoryTransition,
)
from src.plugin_api.runtime_client import PluginRuntimeClient, PluginRuntimeRequestError

PLUGIN = "example.rich"
EVENT_TYPE = f"{PLUGIN}.update"
LIFECYCLE = StoryLifecycleDeclaration(
    contract_version=NOTIFICATION_CONTRACT_VERSION,
    event_type=EVENT_TYPE,
    phases=(
        StoryPhase(id="available", label="Available"),
        StoryPhase(id="installed", label="Done", terminal=True),
    ),
    initial_phases=("available",),
    transitions=(
        StoryTransition(from_phase="available", to_phase="available"),
        StoryTransition(from_phase="available", to_phase="installed"),
    ),
)
SECRET_FACT = "internal-build-7731"


def rich(**changes):
    document = {
        "title": "Update ready",
        "summary": "Version **2.0** is available.",
        "facts": [
            {"key": "build", "label": "Build", "value": {"kind": "text", "value": SECRET_FACT}}
        ],
        "actions": [
            {
                "id": "install",
                "label": "Install now",
                "target": {
                    "kind": "plugin_action",
                    "action_id": "install.apply",
                    "input": {"channel": "stable"},
                },
            },
            {
                "id": "wipe",
                "label": "Wipe",
                "style": "danger",
                "target": {"kind": "plugin_action", "action_id": "wipe.all", "input": {}},
            },
            {
                "id": "notes",
                "label": "Notes",
                "target": {"kind": "link", "url": "https://example.com/n"},
            },
        ],
        "images": [
            {"source": {"kind": "external", "url": "https://img.example.com/a.png"}, "alt": "Cover"}
        ],
    }
    document.update(changes)
    return NotificationContent.model_validate(document)


class World(SimpleNamespace):
    async def emit(self, content=None, *, phase="available", key="story-1", dedupe=None):
        async with SessionLocal() as db:
            notice_id = await emit_interpreted(
                db,
                NotificationDraft(
                    kind="plugin",
                    user_id=self.owner,
                    entity_type="plugin",
                    entity_id=uuid4(),
                    occurred_at=int(time.time()),
                    dedupe_key=dedupe or f"{PLUGIN}:{uuid4()}",
                    title="unused",
                    body="unused",
                    source=PLUGIN,
                    story=StoryReference(key, phase, LIFECYCLE),
                    content=content or rich(),
                ),
                NotificationInterpretation(
                    EVENT_TYPE, Trust.PRIVATE, "standard", "info", self.installation
                ),
            )
            await db.commit()
            story = await db.scalar(
                select(NotificationStory).where(NotificationStory.user_id == self.owner)
            )
            return notice_id, story.id

    def as_user(self, user_id):
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)


@pytest.fixture(name="world")
async def notification_world():
    owner, stranger, installation = uuid4(), uuid4(), uuid4()
    async with SessionLocal() as db:
        for user in (owner, stranger):
            db.add(
                User(
                    id=user,
                    username=user.hex,
                    email=f"{user}@example.test",
                    password_hash="unused",
                )
            )
        await db.commit()
        db.add(
            PluginNotificationTypeRegistration(
                plugin_id=PLUGIN,
                installation_id=installation,
                event_type=EVENT_TYPE,
                definition={},
                registered_at=int(time.time()),
            )
        )
        db.add(
            PluginPermissionGrant(
                plugin_id=PLUGIN,
                installation_id=installation,
                user_id=owner,
                capability="notifications.emit",
                capability_version=1,
            )
        )
        await db.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        state = World(owner=owner, stranger=stranger, installation=installation, client=client)
        state.as_user(owner)
        try:
            yield state
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            async with SessionLocal() as db:
                await db.execute(delete(User).where(User.id.in_((owner, stranger))))
                await db.execute(
                    delete(PluginNotificationTypeRegistration).where(
                        PluginNotificationTypeRegistration.event_type == EVENT_TYPE
                    )
                )
                await db.execute(
                    delete(PluginPermissionGrant).where(PluginPermissionGrant.plugin_id == PLUGIN)
                )
                await db.commit()


async def link_for(world, story_id, **body):
    response = await world.client.post(
        f"/api/notifications/stories/{story_id}/live-links", json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


def token_of(link):
    return link["url"].rsplit("/", 1)[1]


# --- persistence and rejection ---------------------------------------------------------------


async def test_content_is_validated_stored_privately_and_drives_legacy_text(world):
    notice_id, story_id = await world.emit()
    async with SessionLocal() as db:
        notice = await db.get(Notification, notice_id)
        revision = (await db.scalars(select(NotificationStoryRevision))).one()
        assert notice.title == "Update ready"
        assert "Build: " + SECRET_FACT in notice.body and "**" not in notice.body
        assert revision.content["facts"][0]["value"]["value"] == SECRET_FACT
        assert notice.public_title is None and notice.public_body is None


@pytest.mark.parametrize(
    "changes",
    [
        {"summary": "<script>alert(1)</script>"},
        {"blocks": [{"type": "markdown", "markdown": "[x](javascript:alert(1))"}]},
        {"summary": "password=hunter2hunter2"},
        {"summary": "Your verification code is 123456"},
    ],
)
async def test_unsafe_content_is_rejected_without_consuming_the_event(world, changes):
    with pytest.raises(UnsafeContentError):
        await world.emit(rich(**changes), dedupe="rejected")
    async with SessionLocal() as db:
        assert not (
            await db.scalars(
                select(NotificationReceipt).where(NotificationReceipt.dedupe_key == "rejected")
            )
        ).all()
        assert not (await db.scalars(select(NotificationStory))).all()


async def test_reserved_purposes_cannot_carry_rich_content(world):
    async with SessionLocal() as db:
        with pytest.raises(ValueError, match="standard and security"):
            await emit_interpreted(
                db,
                NotificationDraft(
                    kind="plugin",
                    user_id=world.owner,
                    entity_type="plugin",
                    entity_id=uuid4(),
                    occurred_at=int(time.time()),
                    dedupe_key="recovery-rich",
                    title="t",
                    body="b",
                    source=PLUGIN,
                    content=rich(),
                ),  # fmt: skip
                NotificationInterpretation(
                    EVENT_TYPE, Trust.SECURE, "recovery", "info", world.installation
                ),
            )


async def test_event_without_story_becomes_a_single_item_story(world):
    async with SessionLocal() as db:
        await emit_interpreted(
            db,
            NotificationDraft(
                kind="plugin",
                user_id=world.owner,
                entity_type="plugin",
                entity_id=uuid4(),
                occurred_at=int(time.time()),
                dedupe_key="plain",
                title="t",
                body="b",
                source=PLUGIN,
                content=rich(),
            ),  # fmt: skip
            NotificationInterpretation(
                EVENT_TYPE, Trust.PRIVATE, "standard", "info", world.installation
            ),
        )
        await db.commit()
        story = (await db.scalars(select(NotificationStory))).one()
        assert story.phase == "posted" and story.revision == 1


async def test_content_expiry_bounds_the_notification_lifetime(world):
    soon = int(time.time()) + 600
    notice_id, _ = await world.emit(rich(expires_at=soon))
    async with SessionLocal() as db:
        assert (await db.get(Notification, notice_id)).expires_at == soon


# --- owner page ------------------------------------------------------------------------------


async def test_owner_reads_latest_state_and_other_accounts_cannot(world):
    _, story_id = await world.emit()
    page = (await world.client.get(f"/api/notifications/stories/{story_id}")).json()
    assert page["rich"] and page["view"]["title"] == "Update ready"
    assert page["view"]["facts"][0]["text"] == SECRET_FACT
    assert page["view"]["images"][0]["state"] == "proxied"
    assert page["view"]["images"][0]["url"].endswith(f"/{story_id}/images/0")
    assert [a["kind"] for a in page["view"]["actions"]] == ["action", "action", "link"]

    await world.emit(
        rich(title="Update installed", summary="Done."), phase="installed", dedupe="second"
    )
    latest = (await world.client.get(f"/api/notifications/stories/{story_id}")).json()
    assert latest["phase"] == "installed" and latest["revision"] == 2
    assert latest["view"]["title"] == "Update installed"

    world.as_user(world.stranger)
    for path in ("", "/live-links"):
        assert (
            await world.client.get(f"/api/notifications/stories/{story_id}{path}")
        ).status_code == 404
    assert (
        await world.client.post(f"/api/notifications/stories/{story_id}/live-links", json={})
    ).status_code == 404
    assert (
        await world.client.get(f"/api/notifications/stories/{story_id}/images/0")
    ).status_code == 404
    assert (
        await world.client.post(f"/api/notifications/stories/{story_id}/actions/install", json={})
    ).status_code == 404


async def test_story_without_rich_content_falls_back_to_legacy_text(world):
    notice_id, story_id = await world.emit()
    async with SessionLocal() as db:
        await db.execute(update(NotificationStoryRevision).values(content=None))
        await db.commit()
    page = (await world.client.get(f"/api/notifications/stories/{story_id}")).json()
    assert page["rich"] is False and page["title"] == "Update ready" and page["view"] is None


async def test_corrupt_stored_content_is_never_rendered(world):
    _, story_id = await world.emit()
    async with SessionLocal() as db:
        await db.execute(
            update(NotificationStoryRevision).values(content={"title": "<script>x</script>"})
        )
        await db.commit()
    page = (await world.client.get(f"/api/notifications/stories/{story_id}")).json()
    assert page["rich"] is False


# --- live links ------------------------------------------------------------------------------


class Outline(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.attrs = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.append((tag, dict(attrs)))


async def test_live_link_shows_only_the_public_projection(world):
    _, story_id = await world.emit()
    link = await link_for(world, story_id)
    token = token_of(link)
    assert link["url"].startswith("http://test/api/notifications/live/nls1_")
    assert len(token) >= 40
    for identifier in (str(story_id), story_id.hex, str(world.owner), world.owner.hex, PLUGIN):
        assert identifier not in token

    world.as_user(world.stranger)  # nothing else is needed: the token is the capability
    page = await world.client.get(f"/api/notifications/live/{token}")
    assert page.status_code == 200
    for private in (
        SECRET_FACT,
        "Version",
        "Install",
        str(world.owner),
        str(story_id),
        PLUGIN,
        "img.example.com",
    ):
        assert private not in page.text
    assert "Notification update" in page.text and "Available" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert page.headers["referrer-policy"] == "no-referrer"
    state = (await world.client.get(f"/api/notifications/live/{token}/state")).json()
    assert (
        state["status"] == "Available" and SECRET_FACT not in str(state) and "story_id" not in state
    )

    async with SessionLocal() as db:
        stored = (await db.scalars(select(NotificationLiveLink))).one()
        assert stored.token_hash != token and token not in stored.token_hash


async def test_live_page_reflects_the_latest_revision_and_uses_public_fields(world):
    _, story_id = await world.emit()
    token = token_of(await link_for(world, story_id))
    await world.emit(rich(title="Installed", summary="Done."), phase="installed", dedupe="two")
    async with SessionLocal() as db:
        await db.execute(
            update(Notification).values(public_title="Release out", public_body="It is out.")
        )
        await db.commit()
    page = (await world.client.get(f"/api/notifications/live/{token}")).text
    assert "Installed" in page and "Release out" in page and "It is out." in page
    assert "Done." not in page  # the canonical summary is private


async def test_live_page_is_a_screen_reader_friendly_document(world):
    _, story_id = await world.emit()
    token = token_of(await link_for(world, story_id))
    html = (await world.client.get(f"/api/notifications/live/{token}")).text
    outline = Outline()
    outline.feed(html)
    assert html.startswith("<!doctype html>") and ("html", {"lang": "en"}) in outline.attrs
    assert outline.tags.count("h1") == 1 and outline.tags.count("main") == 1
    assert ("main", {"aria-labelledby": "story-title"}) in outline.attrs
    assert {"dl", "dt", "dd", "time"} <= set(outline.tags)
    assert not {"script", "img", "iframe", "form", "style"} - {"style"} & set(outline.tags)
    assert "script" not in outline.tags and "img" not in outline.tags
    assert any(tag == "meta" and a.get("name") == "viewport" for tag, a in outline.attrs)
    assert any(tag == "time" and a.get("datetime", "").endswith("Z") for tag, a in outline.attrs)


@pytest.mark.parametrize("token", ["nls1_", "nls1_" + "a" * 43, "x" * 200, "nls1_%00", "NLS1_abc"])
async def test_unknown_tokens_are_uniformly_unavailable(world, token):
    page = await world.client.get(f"/api/notifications/live/{token}")
    assert page.status_code == 404 and "Link unavailable" in page.text
    state = await world.client.get(f"/api/notifications/live/{token}/state")
    assert state.status_code == 404 and state.json() == {"detail": "Link unavailable"}


async def test_expired_links_stop_working_and_lifetime_is_bounded(world):
    _, story_id = await world.emit()
    link = await link_for(world, story_id, days=365)
    now = int(time.time())
    assert link["expires_at"] - now <= 30 * 86400 + 5  # clamped to the host maximum
    token = token_of(link)
    assert (await world.client.get(f"/api/notifications/live/{token}")).status_code == 200
    async with SessionLocal() as db:
        await db.execute(update(NotificationLiveLink).values(expires_at=int(time.time()) - 1))
        await db.commit()
    expired = await world.client.get(f"/api/notifications/live/{token}")
    assert expired.status_code == 404 and "expired" in expired.text
    assert (await world.client.get(f"/api/notifications/live/{token}/state")).status_code == 404


async def test_revoked_links_stop_working_and_revoke_all(world):
    _, story_id = await world.emit()
    first, second = await link_for(world, story_id), await link_for(world, story_id)
    assert (
        await world.client.delete(f"/api/notifications/stories/{story_id}/live-links/{first['id']}")
    ).status_code == 204
    assert (await world.client.get(f"/api/notifications/live/{token_of(first)}")).status_code == 404
    assert (
        await world.client.get(f"/api/notifications/live/{token_of(second)}")
    ).status_code == 200
    assert (
        await world.client.delete(f"/api/notifications/stories/{story_id}/live-links/{first['id']}")
    ).status_code == 404
    listing = (await world.client.get(f"/api/notifications/stories/{story_id}/live-links")).json()[
        "items"
    ]
    assert len(listing) == 2 and all("url" not in item and "token" not in item for item in listing)
    assert (await world.client.post("/api/notifications/stories/live-links/revoke-all")).json() == {
        "revoked": 1
    }
    assert (
        await world.client.get(f"/api/notifications/live/{token_of(second)}")
    ).status_code == 404


async def test_other_accounts_cannot_revoke_or_list_links(world):
    _, story_id = await world.emit()
    link = await link_for(world, story_id)
    world.as_user(world.stranger)
    assert (
        await world.client.delete(f"/api/notifications/stories/{story_id}/live-links/{link['id']}")
    ).status_code == 404
    assert (await world.client.post("/api/notifications/stories/live-links/revoke-all")).json() == {
        "revoked": 0
    }
    assert (await world.client.get(f"/api/notifications/live/{token_of(link)}")).status_code == 200


async def test_deleting_the_item_or_deactivating_the_owner_kills_the_link(world):
    notice_id, story_id = await world.emit()
    token = token_of(await link_for(world, story_id))
    async with SessionLocal() as db:
        await delete_notice(db, world.owner, notice_id)
        await db.commit()
        assert (await db.scalars(select(NotificationStoryRevision))).one().content is None
        assert (await db.scalars(select(NotificationLiveLink))).one().revoked_at is not None
    assert (await world.client.get(f"/api/notifications/live/{token}")).status_code == 404

    _, story_id = await world.emit(dedupe="again", key="story-2")
    async with SessionLocal() as db:
        story = await db.scalar(
            select(NotificationStory).where(NotificationStory.story_key == "story-2")
        )
        link, raw = await pages.create_live_link(db, world.owner, story.id)
        await db.commit()
        await db.execute(update(User).where(User.id == world.owner).values(is_active=False))
        await db.commit()
    assert (await world.client.get(f"/api/notifications/live/{raw}")).status_code == 404


async def test_live_link_count_is_limited(world, monkeypatch):
    _, story_id = await world.emit()
    monkeypatch.setattr(pages, "MAX_ACTIVE_LINKS_PER_STORY", 2)
    await link_for(world, story_id)
    await link_for(world, story_id)
    refused = await world.client.post(f"/api/notifications/stories/{story_id}/live-links", json={})
    assert refused.status_code == 409


async def test_signed_in_owner_sees_private_state_through_their_link(world):
    _, story_id = await world.emit()
    token = token_of(await link_for(world, story_id))
    owner_page = await world.client.get(f"/api/notifications/stories/by-link/{token}")
    assert (
        owner_page.status_code == 200
        and owner_page.json()["view"]["facts"][0]["text"] == SECRET_FACT
    )
    assert owner_page.headers["cache-control"] == "private, no-store"
    world.as_user(world.stranger)
    assert (
        await world.client.get(f"/api/notifications/stories/by-link/{token}")
    ).status_code == 404


def test_link_tokens_are_not_logged():
    import logging

    from src.features.notification_logging import NotificationLinkFilter

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/notifications/live/nls1_secret", "1.1", 200), None,
    )  # fmt: skip
    NotificationLinkFilter().filter(record)
    assert "nls1_secret" not in str(record.args)


# --- plugin actions --------------------------------------------------------------------------


@pytest.fixture
def runtime(monkeypatch):
    calls = []

    async def fake(self, plugin_id, action_id, values, **context):
        calls.append((plugin_id, action_id, values, context))
        return {"ok": True, "message": "Installed 2.0"}

    monkeypatch.setattr(PluginRuntimeClient, "notification_story_action", fake)
    return calls


async def run(world, story_id, action="install", **body):
    return await world.client.post(
        f"/api/notifications/stories/{story_id}/actions/{action}", json=body
    )


async def test_declared_action_runs_with_host_supplied_input(world, runtime):
    _, story_id = await world.emit()
    response = await run(world, story_id, values={"channel": "beta"}, installation_id=str(uuid4()))
    assert response.status_code == 200 and response.json() == {
        "ok": True,
        "message": "Installed 2.0",
    }
    plugin_id, action_id, values, context = runtime[0]
    assert (plugin_id, action_id, values) == (PLUGIN, "install.apply", {"channel": "stable"})
    assert context["user_id"] == str(world.owner) and context["installation_id"] == str(
        world.installation
    )
    assert context["story_id"] == str(story_id) and context["revision"] == 1


async def test_actions_that_are_undeclared_or_not_plugin_actions_are_denied(world, runtime):
    _, story_id = await world.emit()
    assert (await run(world, story_id, "missing")).status_code == 403
    assert (await run(world, story_id, "notes")).status_code == 403  # a link, not a plugin action
    assert runtime == []


async def test_destructive_actions_need_confirmation(world, runtime):
    _, story_id = await world.emit()
    assert (await run(world, story_id, "wipe")).status_code == 403 and runtime == []
    assert (await run(world, story_id, "wipe", confirm=True)).status_code == 200 and len(
        runtime
    ) == 1


async def test_revoked_grant_denies_the_action(world, runtime):
    _, story_id = await world.emit()
    async with SessionLocal() as db:
        await db.execute(update(PluginPermissionGrant).values(revoked_at=int(time.time())))
        await db.commit()
    response = await run(world, story_id)
    assert response.status_code == 403 and "no longer authorised" in response.text and runtime == []


async def test_retired_source_registration_denies_the_action(world, runtime):
    _, story_id = await world.emit()
    async with SessionLocal() as db:
        await db.execute(
            update(PluginNotificationTypeRegistration).values(revoked_at=int(time.time()))
        )
        await db.commit()
    assert (await run(world, story_id)).status_code == 403 and runtime == []


async def test_reinstalled_source_cannot_act_on_old_stories(world, runtime):
    _, story_id = await world.emit()
    async with SessionLocal() as db:
        await db.execute(update(PluginNotificationTypeRegistration).values(installation_id=uuid4()))
        await db.commit()
    assert (await run(world, story_id)).status_code == 403 and runtime == []


async def test_actions_are_denied_to_other_actors_inactive_owners_and_expired_items(world, runtime):
    notice_id, story_id = await world.emit()
    world.as_user(world.stranger)
    assert (await run(world, story_id)).status_code == 404
    world.as_user(world.owner)
    async with SessionLocal() as db:
        await db.execute(
            update(Notification)
            .where(Notification.id == notice_id)
            .values(expires_at=int(time.time()) - 5)
        )
        await db.commit()
    assert (await run(world, story_id)).status_code == 403
    async with SessionLocal() as db:
        await db.execute(
            update(Notification).where(Notification.id == notice_id).values(expires_at=None)
        )
        await db.execute(update(User).where(User.id == world.owner).values(is_active=False))
        await db.commit()
    assert (await run(world, story_id)).status_code == 403
    assert runtime == []


async def test_actions_on_host_stories_and_erased_items_are_denied(world, runtime):
    notice_id, story_id = await world.emit()
    async with SessionLocal() as db:
        await db.execute(update(NotificationStory).values(scope_key="host"))
        await db.commit()
    assert (await run(world, story_id)).status_code == 403
    async with SessionLocal() as db:
        await delete_notice(db, world.owner, notice_id)
        await db.commit()
    assert (await run(world, story_id)).status_code == 404 and runtime == []


async def test_runtime_failure_is_a_bad_gateway_without_details(world, monkeypatch):
    _, story_id = await world.emit()

    async def broken(self, *args, **kwargs):
        raise PluginRuntimeRequestError("secret traceback with token=abc")

    monkeypatch.setattr(PluginRuntimeClient, "notification_story_action", broken)
    response = await run(world, story_id)
    assert response.status_code == 502 and "secret" not in response.text


async def test_action_messages_are_bounded_text(world, monkeypatch):
    _, story_id = await world.emit()

    async def noisy(self, *args, **kwargs):
        return {"message": "x" * 5000}

    monkeypatch.setattr(PluginRuntimeClient, "notification_story_action", noisy)
    assert len((await run(world, story_id)).json()["message"]) == 300

    async def controls(self, *args, **kwargs):
        return {"message": "a\x00b"}

    monkeypatch.setattr(PluginRuntimeClient, "notification_story_action", controls)
    assert (await run(world, story_id)).json()["message"] is None


async def test_link_count_helper_ignores_expired_links(world):
    _, story_id = await world.emit()
    await link_for(world, story_id)
    async with SessionLocal() as db:
        total = await db.scalar(select(func.count()).select_from(NotificationLiveLink))
        assert total == 1
    assert isinstance(story_id, UUID)
