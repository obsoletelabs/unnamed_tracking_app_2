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
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_story import NotificationStoryRevision
from src.features.notification_contract import content_from_notification
from src.features.notification_contributions import contributed_text, selected_contribution
from src.features.notification_diagnostic_fixtures import test_content, variant_of
from src.features.notification_providers.base import (
    MessageAction,
    MessageFact,
    NotificationMessage,
)
from src.features.notification_providers.capabilities import ResolvedProfile
from src.features.notification_rich import format_fact, render_email_html, render_plain
from src.plugin_api.notification_story_contracts import NotificationContent, RendererContribution

ELLIPSIS = "…"
MAX_EMBED_FACTS = 25
FACT_LABEL_CHARS = 100
FACT_TEXT_CHARS = 500
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
    if notification.purpose == "test":
        # Host-authored fixture content, so test sends exercise the real renderer richly.
        return test_content(variant_of(notification.group_key))
    if notification.story_id is not None:
        stored = await db.scalar(
            select(NotificationStoryRevision.content).where(
                NotificationStoryRevision.story_id == notification.story_id,
                NotificationStoryRevision.revision == notification.story_revision,
            )
        )
        if stored:
            try:
                return NotificationContent.model_validate(stored)
            except ValidationError:  # stored under an older contract: use the item's own text
                pass
    try:
        return content_from_notification(notification, projection)
    except ValueError:
        return None


def story_link(base: str | None, notification: Notification | None) -> str | None:
    if not base:
        return None
    suffix = (
        f"/notifications/stories/{notification.story_id}"
        if notification is not None and notification.story_id
        else "/notifications"
    )
    return base.rstrip("/") + suffix


def _body_fields(
    content: NotificationContent,
    profile: ResolvedProfile,
    base: str | None,
    contribution: RendererContribution | None,
    fields: dict,
) -> list[str]:
    """HTML or body text for the provider's surface, honouring an authorised contribution."""
    if "html_email" in profile.capabilities.text_formats:
        fields["html"] = render_email_html(content, base_url=base or "", contribution=contribution)
        return []
    if contribution is None:
        return (
            ["rich_text_plain"]
            if content.blocks or (content.facts and "facts" not in fields)
            else []
        )
    contributed, note = contributed_text(contribution, content, profile)
    if contributed:
        fields["body"] = contributed
    return [note] if note else []


def _embed_facts(content: NotificationContent) -> tuple[MessageFact, ...]:
    shown = [format_fact(fact) for fact in content.facts][:MAX_EMBED_FACTS]
    return tuple(
        MessageFact(_clip(item["label"], FACT_LABEL_CHARS), _clip(item["text"], FACT_TEXT_CHARS))
        for item in shown
    )


def _presentation(
    content: NotificationContent,
    profile: ResolvedProfile,
    link: str | None,
    base: str | None,
    contribution: RendererContribution | None = None,
) -> tuple[dict, list[str]]:
    """Fields a canonical provider receives, narrowed to its declared capabilities."""
    caps = profile.capabilities
    fallbacks: list[str] = []
    # Structured embeds carry facts as labelled fields, so the body does not repeat them.
    embed_facts = caps.rich_embeds and bool(content.facts)
    _, _, text = render_plain(content, facts=not embed_facts).partition("\n\n")
    fields: dict = {"title": content.title, "body": text or content.summary}
    if embed_facts:
        fields["facts"] = _embed_facts(content)
        if len(content.facts) > MAX_EMBED_FACTS:
            fallbacks.append("facts_truncated")
    fallbacks += _body_fields(content, profile, base, contribution, fields)
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


@dataclass(frozen=True)
class _Clipped:
    """Title and body after the profile's limits; base_title excludes any follow-up prefix."""

    base_title: str
    title: str
    body: str
    truncated: bool


async def _presentation_changes(
    db: AsyncSession,
    message: NotificationMessage,
    notification: Notification,
    *,
    projection: str,
    profile: ResolvedProfile,
    link: str | None,
    link_base: str | None,
) -> tuple[dict, list[str]]:
    """Field overrides from the canonical content plus the fallback notes they produced."""
    fallbacks: list[str] = list(profile.notes)
    # Host-composed digests, recovery, invitation, test and verification messages keep their own
    # text: rich content of one story must neither replace a digest nor reach a sensitive mail.
    content = (
        None
        if message.items
        or message.purpose != "standard"
        or message.kind == "destination_verification"
        else await _content(db, notification, projection)
    )
    changes: dict = {}
    if content is not None and profile.canonical:
        fields, notes = _presentation(
            content,
            profile,
            link,
            link_base,
            await selected_contribution(db, notification, profile),
        )
        fallbacks += notes
        changes |= fields
    elif content is not None and (
        content.blocks or content.facts or content.images or content.actions
    ):
        fallbacks.append("rich_content_plain_text")
    changes.setdefault("title", message.title)
    changes.setdefault("body", message.body)
    return changes, fallbacks


def _clip_message(changes: dict, operation: str, caps: Any) -> _Clipped:
    """Pop title and body from the overrides and fit them to the profile limits."""
    title, body = changes.pop("title"), changes.pop("body")
    base_title = _clip(title, caps.max_title_chars)
    clipped_title = _clip(
        f"Update: {title}" if operation == "follow_up" else title, caps.max_title_chars
    )
    budget = caps.max_payload_bytes - PAYLOAD_OVERHEAD - len(clipped_title.encode())
    clipped_body = _clip_bytes(_clip(body, caps.max_text_chars), budget)
    return _Clipped(
        base_title, clipped_title, clipped_body, base_title != title or clipped_body != body
    )


def _rendering_digest(base_title: str, rendered: NotificationMessage) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                base_title,
                rendered.body,
                rendered.html,
                [(a.label, a.url) for a in rendered.actions],
                list(rendered.images),
                [(f.label, f.text) for f in rendered.facts],
                rendered.link,
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


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
    link = None
    if profile.capabilities.links:
        link = story_link(link_base, None if message.items else notification)
    changes, fallbacks = await _presentation_changes(
        db,
        message,
        notification,
        projection=projection,
        profile=profile,
        link=link,
        link_base=link_base,
    )
    clipped = _clip_message(changes, operation, profile.capabilities)
    if clipped.truncated:
        fallbacks.append("truncated")
    rendered = replace(
        message,
        title=clipped.title,
        body=clipped.body,
        link=link,
        operation=operation,
        remote_ref=remote_ref
        if operation == "edit" or profile.capabilities.threaded_follow_ups
        else None,
        revision=revision,
        idempotency_key=str(idempotency_key),
        **changes,
    )
    return Rendering(
        rendered,
        tuple(dict.fromkeys(fallbacks)),
        _rendering_digest(clipped.base_title, rendered),
    )
