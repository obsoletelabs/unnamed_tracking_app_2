"""Rich standalone SMTP mail: sanitised HTML, text parity, live links, follow-ups and digests."""

import re
import time
from email.message import EmailMessage
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.core.config import settings
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_story import NotificationStoryRevision
from src.database.session import SessionLocal
from src.features.notification_controller import emit_legacy_rows
from src.features.notification_enrollment import create_email
from src.features.notification_providers.base import NotificationMessage
from src.features.notification_providers.capabilities import resolve_profile
from src.features.notification_providers.delivery import process_pending_deliveries
from src.features.notification_providers.email_rendering import render_email, render_plain_email
from src.features.notification_providers.smtp import SmtpNotificationProvider
from src.features.notification_stories import PLUGIN_UPDATE_LIFECYCLE, StoryReference
from src.features.notification_unsubscribe import UnsubscribeError, unsubscribe_email
from src.plugin_api.notification_story_contracts import NotificationContent
from tests import test_notification_email as email_tests
from tests.test_notification_email import (  # noqa: F401
    draft,
    link_token,
    release_windows,
    verify,
)
from tests.test_notification_story_pages import (  # noqa: F401
    SECRET_FACT,
    notification_world,
    rich,
)

STORY_PAGE = re.compile(r"/notifications/stories/([0-9a-f-]{36})")


mailbox = email_tests.mailbox


@pytest.fixture(autouse=True)
def public_origin(monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test")


async def subscribe(world, mailbox, address="owner@example.test"):
    """A verified email destination for the story owner; verification mail is discarded."""
    async with SessionLocal() as db:
        endpoint = await create_email(db, world.owner, address, "Home")
        await verify(db, world.owner, endpoint, mailbox)
    mailbox.clear()
    return endpoint


async def deliver_all():
    async with SessionLocal() as db:
        await release_windows(db)
        return await process_pending_deliveries(db)


def parts(mail: EmailMessage) -> tuple[str, str]:
    plain = mail.get_body(preferencelist=("plain",)).get_content()
    html = mail.get_body(preferencelist=("html",)).get_content()
    return plain, html


def visible_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_smtp_profile_is_canonical_and_cannot_edit():
    profile = resolve_profile(SmtpNotificationProvider())
    caps = profile.capabilities
    assert profile.canonical and not profile.can_edit
    assert "html_email" in caps.text_formats and caps.live_page_links and caps.max_batch_items > 1
    assert caps.images is not None and not caps.images.proxied_urls  # never hosted by the app


async def test_story_email_has_sanitised_html_and_equivalent_text(world, mailbox):
    await subscribe(world, mailbox)
    content = rich(summary="Version **2.0** is available for a < b & c")
    await world.emit(content)
    assert await deliver_all() == 1
    assert len(mailbox) == 1
    plain, html = parts(mailbox[0])
    lowered = html.lower()
    assert "<script" not in lowered and "onerror" not in lowered and "javascript:" not in lowered
    assert lowered.startswith("<!doctype html>") and "<strong>2.0</strong>" in html
    text = visible_text(html)
    for essential in ("Update ready", "Version 2.0 is available", "Build", SECRET_FACT):
        assert essential in text and essential in plain
    # Actions that leave the app are links in both parts; plugin actions stay in the app.
    assert "https://example.com/n" in html and "Notes: https://example.com/n" in plain
    assert "a &lt; b &amp; c" in html and "a < b & c" in plain
    assert "Install now" not in html and "wipe" not in lowered
    # The live link points to the signed-in page and appears in both parts.
    page = STORY_PAGE.search(plain)
    assert page and f"https://app.example.test/notifications/stories/{page.group(1)}" in html
    assert mailbox[0]["Subject"] == "Update ready"


async def test_images_are_never_required_and_only_linked_when_enabled(world, mailbox, monkeypatch):
    await subscribe(world, mailbox)
    await world.emit(rich(), key="no-images")
    await deliver_all()
    plain, html = parts(mailbox.pop())
    assert "<img" not in html.lower()  # default image mode never links remote artwork
    assert "Update ready" in plain and SECRET_FACT in plain and SECRET_FACT in html
    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MODE", "external")
    await world.emit(rich(), key="with-images", dedupe="images-on")
    await deliver_all()
    plain, html = parts(mailbox.pop())
    assert html.count("<img") == 1 and 'src="https://img.example.com/a.png"' in html
    assert 'alt="' in html and SECRET_FACT in plain  # alt text and the same facts without images
    assert "img.example.com" not in plain


async def test_live_link_shows_current_state_for_the_owner_only_until_it_expires(world, mailbox):
    await subscribe(world, mailbox)
    _, story = await world.emit(rich(title="Build 1"), key="live")
    await deliver_all()
    plain, _ = parts(mailbox.pop())
    assert f"/notifications/stories/{story}" in plain
    # A later revision is what the link shows, whatever the delivered email said.
    await world.emit(rich(title="Build 2"), key="live", dedupe="live-2")
    api = f"/api/notifications/stories/{story}"
    response = await world.client.get(api)
    assert response.status_code == 200 and response.json()["title"] == "Build 2"
    world.as_user(world.stranger)
    assert (await world.client.get(api)).status_code == 404
    world.as_user(world.owner)
    async with SessionLocal() as db:
        revision = await db.scalar(
            select(NotificationStoryRevision).where(
                NotificationStoryRevision.story_id == story,
                NotificationStoryRevision.revision == 2,
            )
        )
        revision.content = {**revision.content, "expires_at": int(time.time()) - 5}
        await db.commit()
    stale = (await world.client.get(api)).json()
    assert stale["view"]["stale"] is True


async def test_followups_are_selective_and_mail_is_never_edited(world, mailbox, monkeypatch):
    await subscribe(world, mailbox)
    await world.emit(rich(title="Build 1"), key="follow")
    await deliver_all()
    assert len(mailbox) == 1
    # A trivial edit (default policy: only important changes) sends nothing and edits nothing.
    await world.emit(rich(title="Build 1 typo fixed"), key="follow", dedupe="follow-2")
    await deliver_all()
    assert len(mailbox) == 1
    monkeypatch.setattr(settings, "NOTIFICATION_FOLLOW_UPS", "all")
    await world.emit(rich(title="Build 1 changed"), key="follow", dedupe="follow-3")
    await deliver_all()
    assert len(mailbox) == 2
    assert mailbox[1]["Subject"] == "Update: Build 1 changed"
    assert mailbox[1]["In-Reply-To"] is None  # a new message, never a modification
    monkeypatch.setattr(settings, "NOTIFICATION_FOLLOW_UPS", "none")
    await world.emit(rich(title="Build 1 again"), key="follow", dedupe="follow-4")
    await deliver_all()
    assert len(mailbox) == 2


async def test_digest_is_one_mail_with_every_item_in_html_and_text(world, mailbox):
    endpoint = await subscribe(world, mailbox)
    async with SessionLocal() as db:
        rows = [
            {
                **draft("plugin_update"),
                "title": f"Plugin {name} updated",
                "body": f"{name} 2.0 is ready",
                "story": StoryReference(
                    key=f"update:{name}:2.0.0", phase="available", lifecycle=PLUGIN_UPDATE_LIFECYCLE
                ),
            }
            for name in ("Alpha", "Beta", "Gamma")
        ]
        await emit_legacy_rows(db, world.owner, [{k: v for k, v in r.items()} for r in rows])
        await db.commit()
    assert await deliver_all() == 1
    digest = [mail for mail in mailbox if "3 " in (mail["Subject"] or "")]
    assert len(mailbox) == 1 and digest, [m["Subject"] for m in mailbox]
    plain, html = parts(mailbox[0])
    for name in ("Alpha", "Beta", "Gamma"):
        assert f"Plugin {name} updated" in plain and f"Plugin {name} updated" in html
        assert f"{name} 2.0 is ready" in plain and f"{name} 2.0 is ready" in html
    assert html.count("<h2") == 3 and html.count("View the latest status") == 3
    assert mailbox[0]["To"] == "owner@example.test"
    assert endpoint


async def test_unsubscribe_from_rich_mail_only_affects_its_destination(world, mailbox):
    first = await subscribe(world, mailbox, "first@example.test")
    async with SessionLocal() as db:
        second = await create_email(db, world.owner, "second@example.test", "Work")
        await verify(db, world.owner, second, mailbox)
    mailbox.clear()
    await world.emit(rich(), key="unsub")
    await deliver_all()
    mails = {mail["To"]: mail for mail in mailbox}
    assert set(mails) == {"first@example.test", "second@example.test"}
    plain, html = parts(mails["first@example.test"])
    token = link_token(mails["first@example.test"]["List-Unsubscribe"].strip("<>"))
    assert token not in plain and token in html  # the bearer link is not in plain text
    async with SessionLocal() as db:
        await unsubscribe_email(db, token)
        other = await db.get(NotificationDestination, second)
        stopped = await db.get(NotificationDestination, first)
        assert other.enabled and stopped.enabled  # type-scoped opt-out, no destination toggled
    mailbox.clear()
    await world.emit(rich(title="Next"), key="unsub-2")
    await deliver_all()
    assert [mail["To"] for mail in mailbox] == ["second@example.test"]
    with pytest.raises(UnsubscribeError):
        async with SessionLocal() as db:
            await unsubscribe_email(db, "not-a-token")


def test_plain_text_has_title_body_actions_and_live_link():
    content = NotificationContent.model_validate({"title": "T", "summary": "S"})
    assert content.title == "T"
    message = NotificationMessage(
        id=uuid4(),
        kind="plugin",
        title="Hello",
        body="Body text",
        media_type="system",
        media_id=uuid4(),
        event_at=1,
        html="<p>Body text</p>",
        link="https://app.example.test/notifications/stories/x",
    )
    plain = render_plain_email(message, "https://app.example.test/unsub?token=abc")
    assert plain.startswith("Hello\n\nBody text")
    assert "Latest status: https://app.example.test/notifications/stories/x" in plain
    assert "token=abc" not in plain and "Unsubscribe" in plain


def test_html_is_sanitised_again_before_it_reaches_the_wire(mailbox_message=None):
    hostile = (
        '<p onclick="x()">Hi</p><script>alert(1)</script><img src="javascript:alert(1)">'
        '<a href="javascript:alert(2)">bad</a><iframe src="https://evil.test"></iframe>'
    )
    message = NotificationMessage(
        id=uuid4(),
        kind="plugin",
        title="Hostile",
        body="Hi",
        media_type="system",
        media_id=uuid4(),
        event_at=1,
        html=hostile,
        images=("javascript:alert(3)",),
    )
    html = render_email(message).lower()
    for forbidden in ("<script", "onclick", "javascript:", "<iframe", "alert("):
        assert forbidden not in html
    assert "hi" in html
