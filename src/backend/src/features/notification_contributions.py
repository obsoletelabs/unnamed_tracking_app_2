"""Plugin render contributions applied at dispatch, after every authorisation is rechecked.

A contribution only re-arranges canonical content the host already accepted; it never adds data,
never applies to a PUBLIC or shared projection, and its output passes the same sanitiser and
bounds as host rendering. It is looked up from the *current* registration and grants for each
physical operation, so a revoked grant or a disabled installation falls back to host rendering.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification import Notification
from src.features.notification_canonical_registry import active_canonical_registration
from src.features.notification_markdown import plain_text, render_markdown
from src.features.notification_providers.capabilities import ResolvedProfile
from src.features.notification_rich import apply_template, format_fact
from src.features.notification_safety import markdown_is_safe
from src.features.notification_story_pages import contribution_authorised
from src.plugin_api.notification_story_contracts import (
    NotificationContent,
    RendererContribution,
)


def surface_for(profile: ResolvedProfile) -> str:
    formats = profile.capabilities.text_formats
    if "html_email" in formats:
        return "email"
    if profile.capabilities.rich_embeds or "markdown" in formats:
        return "chat"
    return "text"


async def selected_contribution(
    db: AsyncSession, notification: Notification, profile: ResolvedProfile
) -> RendererContribution | None:
    """First declared, currently authorised contribution for the provider's surface."""
    if (
        notification.source_installation_id is None
        or notification.user_id is None
        or not profile.canonical
    ):
        return None
    registration = await active_canonical_registration(
        db, notification.source, notification.source_installation_id, notification.event_type
    )
    if registration is None:
        return None
    surface = surface_for(profile)
    for contribution in registration.renderers:
        if contribution.surface != surface:
            continue
        if await contribution_authorised(
            db,
            contribution,
            plugin_id=notification.source,
            installation_id=notification.source_installation_id,
            user_id=notification.user_id,
        ):
            return contribution
    return None


def _field_text(content: NotificationContent, field: str) -> str:
    if field == "title":
        return content.title
    if field == "summary":
        return plain_text(render_markdown(content.summary))
    if field == "blocks":
        return "\n\n".join(
            plain_text(render_markdown(getattr(block, "markdown", getattr(block, "text", ""))))
            for block in content.blocks
        )
    if field == "facts":
        return "\n".join(
            f"{fact['label']}: {fact['text']}" for fact in map(format_fact, content.facts)
        )
    if field == "attribution" and content.attribution:
        return content.attribution.label
    return ""


def contributed_text(
    contribution: RendererContribution, content: NotificationContent, profile: ResolvedProfile
) -> tuple[str | None, str | None]:
    """(body, fallback note) for a non-email surface. Markdown only if the provider can show it."""
    if contribution.kind == "fields":
        parts = [_field_text(content, name) for name in contribution.fields]
        return "\n\n".join(part for part in parts if part), None
    if contribution.kind == "markdown_template":
        source = apply_template(contribution, content)
        if "markdown" in profile.capabilities.text_formats:
            if markdown_is_safe(source):
                return source, None
            return plain_text(render_markdown(source)), "contribution_unsafe_markdown"
        return plain_text(render_markdown(source)), "contribution_markdown_to_plain"
    return None, "contribution_unsupported"
