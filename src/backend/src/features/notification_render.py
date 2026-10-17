"""Capability-negotiated rendering of one notification for one physical provider operation.

The dispatcher renders per attempt from the provider's *current* profile. Private canonical content
is rendered only for the canonical projection; public and media-shared projections keep their
host-approved text. Anything a provider cannot present is dropped to an explicit fallback whose
code is stored on the delivery for diagnostics.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_story import NotificationStoryRevision
from src.features.notification_contract import content_from_notification
from src.features.notification_providers.base import MessageAction, NotificationMessage
from src.features.notification_providers.capabilities import ResolvedProfile
from src.features.notification_rich import render_email_html, render_plain
from src.plugin_api.notification_story_contracts import NotificationContent

ELLIPSIS = "…"
PAYLOAD_OVERHEAD = 512


@dataclass(frozen=True)
class Rendering:
    message: NotificationMessage
    fallbacks: tuple[str, ...]
    content_hash: str


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(limit - 1, 0)].rstrip() + ELLIPSIS


def _clip_bytes(value: str, limit: int) -> str:
    if len(value.encode()) <= limit:
        return value
    clipped = value.encode()[: max(limit - 3, 0)].decode(errors="ignore").rstrip()
    return clipped + ELLIPSIS


async def _content(
    db: AsyncSession, notification: Notification, projection: str
) -> NotificationContent | None:
    """Canonical content of the notification's current revision (private projection only)."""
    if projection != "canonical":
        return None
    if notification.story_id is not None:
        stored = await db.scalar(
            select(NotificationStoryRevision.content).where(
                NotificationStoryRevision.story_id == notification.story_id,
                NotificationStoryRevision.revision == notification.story_revision,
            )
        )
        if stored:
            return NotificationContent.model_validate(stored)
    try:
        return content_from_notification(notification, projection)
    except ValueError:
        return None


def story_link(base: str | None, notification: Notification) -> str | None:
    if not base:
        return None
    suffix = (
        f"/notifications/stories/{notification.story_id}"
        if notification.story_id
        else "/notifications"
    )
    return base.rstrip("/") + suffix


def _presentation(
    content: NotificationContent,
    profile: ResolvedProfile,
    link: str | None,
    base: str | None,
) -> tuple[dict, list[str]]:
    """Fields a canonical provider receives, narrowed to its declared capabilities."""
    caps = profile.capabilities
    fallbacks: list[str] = []
    body = render_plain(content).split("\n\n", 1)
    text = body[1] if len(body) > 1 else content.summary
    fields: dict = {"title": content.title, "body": text}
    if "html_email" in caps.text_formats:
        fields["html"] = render_email_html(content, base_url=base or "")
    elif content.blocks or content.facts:
        fallbacks.append("rich_text_plain")
    buttons: list[MessageAction] = []
    for action in content.actions:
        target = action.target
        url = (
            target.url
            if target.kind == "link"
            else f"{base.rstrip('/')}{target.path}"
            if target.kind == "host_route" and base
            else None
        )
        if url and len(buttons) < caps.action_buttons:
            buttons.append(MessageAction(action.label, url))
    if content.actions and len(buttons) < len(content.actions):
        # Plugin actions never leave the signed-in app; the story page offers every action.
        fallbacks.append("actions_via_story_link" if link else "actions_dropped")
    fields["actions"] = tuple(buttons)
    images: list[str] = []
    if (
        caps.images is not None
        and caps.images.external_urls
        and settings.NOTIFICATION_IMAGE_MODE == "external"
    ):
        for image in content.images:
            if image.source.kind == "external" and len(images) < caps.images.max_images:
                images.append(image.source.url)
    if content.images and len(images) < len(content.images):
        fallbacks.append("images_dropped")
    fields["images"] = tuple(images)
    return fields, fallbacks


async def render_message(
    db: AsyncSession,
    message: NotificationMessage,
    notification: Notification,
    *,
    projection: str,
    profile: ResolvedProfile,
    operation: str,
    remote_ref: str | None,
    link_base: str | None,
    revision: int,
    idempotency_key: UUID | str,
) -> Rendering:
    """Apply the profile to a base message. Pure apart from reading the revision's content."""
    caps = profile.capabilities
    fallbacks: list[str] = list(profile.notes)
    link = story_link(link_base, notification) if caps.links else None
    content = await _content(db, notification, projection)
    changes: dict = {}
    if content is not None and profile.canonical:
        fields, notes = _presentation(content, profile, link, link_base)
        fallbacks += notes
        changes |= fields
    elif content is not None and (
        content.blocks or content.facts or content.images or content.actions
    ):
        fallbacks.append("rich_content_plain_text")
    title = changes.pop("title", message.title)
    body = changes.pop("body", message.body)
    base_title = _clip(title, caps.max_title_chars)
    clipped_title = _clip(
        f"Update: {title}" if operation == "follow_up" else title, caps.max_title_chars
    )
    clipped_body = _clip(body, caps.max_text_chars)
    budget = caps.max_payload_bytes - PAYLOAD_OVERHEAD - len(clipped_title.encode())
    clipped_body = _clip_bytes(clipped_body, budget)
    if base_title != title or clipped_body != body:
        fallbacks.append("truncated")
    rendered = replace(
        message,
        title=clipped_title,
        body=clipped_body,
        link=link,
        operation=operation,
        remote_ref=remote_ref if operation == "edit" or caps.threaded_follow_ups else None,
        revision=revision,
        idempotency_key=str(idempotency_key),
        **changes,
    )
    digest = hashlib.sha256(
        json.dumps(
            [
                base_title,
                rendered.body,
                rendered.html,
                [(a.label, a.url) for a in rendered.actions],
                list(rendered.images),
                rendered.link,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return Rendering(rendered, tuple(dict.fromkeys(fallbacks)), digest)
