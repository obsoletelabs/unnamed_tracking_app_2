"""What a browser/PWA notification may show on a device, decided by the host.

The Web Push payload carries only opaque identities. The service worker asks the signed-in app
origin for the preview, so account state, consent, revocation and the projection chosen for this
destination are rechecked at display time. Anything the platform may not render, or that a
locked screen should not reveal, falls back to a constant generic message.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.features.notification_markdown import plain_text, render_markdown
from src.features.notification_providers.base import NotificationMessage
from src.features.notification_render import _content
from src.features.notification_safety import CREDENTIAL_PATTERNS
from src.plugin_api.notification_story_contracts import NotificationContent

GENERIC_TITLE = "New notification"
GENERIC_BODY = "Open Unnamed Tracking to view your notifications."
MAX_TITLE = 80
MAX_BODY = 240
MAX_ACTIONS = 2
MAX_PAYLOAD_BYTES = 1024
# A delivery in one of these states is (or just was) a live obligation for this device.
LIVE_DELIVERY = ("processing", "sent", "retry_wait")
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]+")
OPERATIONS = ("send", "edit", "follow_up")


def story_identity(notification: Notification) -> str:
    """Stable identity of what the user sees: the story, or the lone item outside a story."""
    return (
        f"story:{notification.story_id}"
        if notification.story_id
        else f"notification:{notification.id}"
    )


def push_topic(endpoint_id: UUID, identity: str) -> str:
    """RFC 8030 Topic: lets the push service replace an undelivered alert for the same story."""
    digest = hashlib.sha256(f"{endpoint_id}:{identity}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:32]


def build_envelope(
    endpoint: NotificationDestination,
    generation: str,
    message: NotificationMessage,
    notification: Notification | None,
) -> dict:
    """Content-free payload: identities only, never a title, body, link or address."""
    envelope: dict = {
        "version": 1,
        "destination": str(endpoint.id),
        "revision": endpoint.revision,
        "generation": generation,
    }
    if notification is None:
        return envelope
    envelope["notification"] = str(notification.id)
    envelope["operation"] = message.operation if message.operation in OPERATIONS else "send"
    if notification.story_id is not None:
        envelope["story"] = str(notification.story_id)
    envelope["story_revision"] = max(message.revision, notification.story_revision or 0, 0)
    return envelope


def _line(value: str, limit: int) -> str:
    text = " ".join(_CONTROL.sub(" ", value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _safe_text(value: str) -> bool:
    return not any(pattern.search(value) for pattern in CREDENTIAL_PATTERNS)


@dataclass(frozen=True)
class Preview:  # pylint: disable=too-many-instance-attributes
    generic: bool
    url: str
    story_revision: int
    title: str = GENERIC_TITLE
    body: str = GENERIC_BODY
    image: str | None = None
    actions: tuple[tuple[str, str, str], ...] = ()
    fallbacks: tuple[str, ...] = ()

    def as_json(self) -> dict:
        if self.generic:
            return {
                "generic": True,
                "title": GENERIC_TITLE,
                "body": GENERIC_BODY,
                "url": self.url,
                "story_revision": self.story_revision,
            }
        return {
            "generic": False,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "story_revision": self.story_revision,
            "image": self.image,
            "actions": [
                {"action": action, "title": title, "url": url}
                for action, title, url in self.actions
            ],
        }


def _app_path(path: str) -> str | None:
    """Same-origin absolute path only: no scheme, authority, backslash or traversal."""
    if not path.startswith("/") or path.startswith("//") or "\\" in path or len(path) > 512:
        return None
    if any(ord(character) <= 32 or ord(character) == 127 for character in path):
        return None
    if ".." in path.split("?", 1)[0].split("/") or urlsplit(path).netloc:
        return None
    return path


def _actions(content: NotificationContent | None) -> tuple[tuple[str, str, str], ...]:
    found: list[tuple[str, str, str]] = []
    for action in content.actions if content else ():
        target = action.target
        path = _app_path(target.path) if target.kind == "host_route" else None
        label = _line(action.label, 40)
        if path and label and len(found) < MAX_ACTIONS:
            found.append((action.id, label, path))
    return tuple(found)


def _image(content: NotificationContent | None) -> str | None:
    """Notification images are loaded by the OS without our session, so only direct URLs."""
    if content is None or settings.NOTIFICATION_IMAGE_MODE != "external":
        return None
    for image in content.images:
        if image.source.kind == "external" and urlsplit(image.source.url).scheme == "https":
            return image.source.url
    return None


async def preview_for(
    db: AsyncSession, endpoint: NotificationDestination, notification: Notification
) -> Preview | None:
    """None when the item must not be shown at all (gone, expired, not routed to this device)."""
    now = int(time.time())
    if (
        notification.user_id != endpoint.user_id
        or notification.deleted_at is not None
        or (notification.expires_at is not None and notification.expires_at <= now)
    ):
        return None
    delivery = await db.scalar(
        select(NotificationDelivery)
        .where(
            NotificationDelivery.notification_id == notification.id,
            NotificationDelivery.destination_id == endpoint.id,
            NotificationDelivery.destination_revision == endpoint.revision,
            NotificationDelivery.status.in_(LIVE_DELIVERY),
        )
        .order_by(NotificationDelivery.story_revision.desc())
        .limit(1)
    )
    if delivery is None:
        return None
    url = (
        f"/notifications/stories/{notification.story_id}"
        if notification.story_id
        else "/notifications"
    )
    revision = notification.story_revision or 0
    if (
        settings.NOTIFICATION_BROWSER_PREVIEWS != "story"
        or delivery.projection != "canonical"
        or notification.purpose != "standard"
    ):
        return Preview(True, url, revision)
    content = await _content(db, notification, delivery.projection)
    title = _line(content.title if content else notification.title, MAX_TITLE)
    source = content.summary if content and content.summary else notification.body
    # Summaries and bodies are Markdown; a device shows them as plain text.
    body = _line(plain_text(render_markdown(source)), MAX_BODY)
    if not title or not _safe_text(f"{title}\n{source}\n{body}"):
        return Preview(True, url, revision, fallbacks=("preview_withheld",))
    return Preview(
        False,
        url,
        revision,
        title=title,
        body=body,
        image=_image(content),
        actions=_actions(content),
    )
