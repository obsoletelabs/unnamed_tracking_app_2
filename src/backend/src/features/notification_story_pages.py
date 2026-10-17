"""Host-owned latest-state story pages, live links and reauthorised plugin actions.

The page always shows the *latest* revision of a story. Two audiences exist and never share data:
- the signed-in owner sees the private canonical content;
- a bearer of a live link sees only the PUBLIC projection (host-approved public fields and the
  phase), so a forwarded or leaked link cannot expose private facts, recipients or credentials.
"""

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_story import (
    HOST_SCOPE,
    NotificationLiveLink,
    NotificationStory,
    NotificationStoryRevision,
)
from src.database.models.plugin_notification_type import PluginNotificationTypeRegistration
from src.database.models.user import User
from src.features.notification_images import effective_image_mode, resolve_images
from src.features.notification_rich import build_view, humanize_phase, public_projection
from src.plugin_api.grants import has_capability_grant
from src.plugin_api.notification_story_contracts import (
    NotificationContent,
    PluginActionTarget,
    RendererContribution,
)
from src.plugin_api.runtime_client import (
    PluginRuntimeClient,
    PluginRuntimeRequestError,
    PluginRuntimeUnavailable,
)

TOKEN_PREFIX = "nls1_"
MAX_ACTIVE_LINKS_PER_STORY = 20
LIVE_PATH = "/api/notifications/live"
# The source's reviewed emit grant is the action permission until #115 defines finer scopes.
ACTION_CAPABILITY = "notifications.emit"
_LAST_USED_GRANULARITY = 60


class StoryPageError(Exception):
    """Base error; the message is safe to show and never names accounts or credentials."""


class StoryNotFound(StoryPageError):
    """Unknown, foreign, erased, expired or revoked: indistinguishable to the caller."""


class ActionDenied(StoryPageError):
    """A reauthorisation check failed."""


class ActionUnavailable(StoryPageError):
    """The plugin runtime could not run the action."""


@dataclass(frozen=True)
class StoryState:
    story: NotificationStory
    item: Notification
    revision: NotificationStoryRevision


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def live_url(origin: str, token: str) -> str:
    return f"{origin}{LIVE_PATH}/{token}"


async def latest_state(
    db: AsyncSession, story: NotificationStory, *, lock: bool = False
) -> StoryState | None:
    """The story's head item and revision, or None when the head was erased."""
    statement = select(Notification).where(
        Notification.story_id == story.id,
        Notification.story_revision == story.revision,
        Notification.user_id == story.user_id,
        Notification.deleted_at.is_(None),
    )
    item = await db.scalar(statement.with_for_update() if lock else statement)
    if item is None:
        return None
    revision = await db.scalar(
        select(NotificationStoryRevision).where(
            NotificationStoryRevision.story_id == story.id,
            NotificationStoryRevision.revision == story.revision,
        )
    )
    return StoryState(story, item, revision) if revision is not None else None


async def owned_state(db: AsyncSession, user_id: UUID, story_id: UUID) -> StoryState:
    story = await db.scalar(
        select(NotificationStory).where(
            NotificationStory.id == story_id, NotificationStory.user_id == user_id
        )
    )
    state = await latest_state(db, story) if story is not None else None
    if state is None:
        raise StoryNotFound("Story not found")
    return state


def parse_content(revision: NotificationStoryRevision) -> NotificationContent | None:
    if not revision.content:
        return None
    try:
        return NotificationContent.model_validate(revision.content)
    except ValidationError:  # stored before a stricter contract: never render unvalidated data
        return None


def story_summary(state: StoryState) -> dict[str, Any]:
    return {
        "story_id": state.story.id,
        "revision": state.story.revision,
        "phase": state.story.phase,
        "status": humanize_phase(state.story.phase),
        "updated_at": state.story.updated_at,
        "event_type": state.story.event_type,
        "source": state.story.source,
        "notification_id": state.item.id,
        "severity": state.item.severity,
    }


async def owner_view(
    db: AsyncSession, user_id: UUID, story_id: UUID, *, proxy_path: str
) -> dict[str, Any]:
    """Private latest state for the signed-in owner, with policy-resolved images."""
    state = await owned_state(db, user_id, story_id)
    now = int(time.time())
    content = parse_content(state.revision)
    summary = story_summary(state)
    if content is None:
        # Pre-rich story or erased document: the legacy title/body text, escaped by clients.
        return {
            **summary,
            "rich": False,
            "title": state.item.title,
            "text": state.item.body,
            "view": None,
        }
    images = await resolve_images(
        db, content, user_id=user_id, proxy_path=proxy_path, mode=effective_image_mode()
    )
    return {
        **summary,
        "rich": True,
        "title": content.title,
        "text": None,
        "view": build_view(content, images=images, now=now),
    }


# --- live links ------------------------------------------------------------------------------


def clamp_lifetime_days(days: int | None) -> int:
    chosen = days or settings.NOTIFICATION_LIVE_LINK_DEFAULT_DAYS
    return max(1, min(chosen, settings.NOTIFICATION_LIVE_LINK_MAXIMUM_DAYS))


async def create_live_link(
    db: AsyncSession, user_id: UUID, story_id: UUID, *, days: int | None = None
) -> tuple[NotificationLiveLink, str]:
    """Mint a link for an owned story; the raw token is returned once and never stored."""
    state = await owned_state(db, user_id, story_id)
    await db.scalar(
        select(NotificationStory.id).where(NotificationStory.id == story_id).with_for_update()
    )
    now = int(time.time())
    active = await db.scalar(
        select(sql_count())
        .select_from(NotificationLiveLink)
        .where(
            NotificationLiveLink.story_id == story_id,
            NotificationLiveLink.revoked_at.is_(None),
            NotificationLiveLink.expires_at > now,
        )
    )
    if (active or 0) >= MAX_ACTIVE_LINKS_PER_STORY:
        raise StoryPageError("Revoke an existing link before creating another")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    link = NotificationLiveLink(
        story_id=state.story.id,
        user_id=user_id,
        token_hash=hash_token(token),
        created_at=now,
        expires_at=now + clamp_lifetime_days(days) * 86400,
    )
    db.add(link)
    await db.flush()
    return link, token


async def list_live_links(
    db: AsyncSession, user_id: UUID, story_id: UUID
) -> list[NotificationLiveLink]:
    await owned_state(db, user_id, story_id)
    rows = await db.scalars(
        select(NotificationLiveLink)
        .where(NotificationLiveLink.story_id == story_id, NotificationLiveLink.user_id == user_id)
        .order_by(NotificationLiveLink.created_at.desc())
    )
    return list(rows)


async def revoke_live_link(db: AsyncSession, user_id: UUID, story_id: UUID, link_id: UUID) -> bool:
    result = await db.execute(
        update(NotificationLiveLink)
        .where(
            NotificationLiveLink.id == link_id,
            NotificationLiveLink.story_id == story_id,
            NotificationLiveLink.user_id == user_id,
            NotificationLiveLink.revoked_at.is_(None),
        )
        .values(revoked_at=int(time.time()))
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def revoke_all_live_links(db: AsyncSession, user_id: UUID) -> int:
    result = await db.execute(
        update(NotificationLiveLink)
        .where(NotificationLiveLink.user_id == user_id, NotificationLiveLink.revoked_at.is_(None))
        .values(revoked_at=int(time.time()))
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def resolve_live_link(db: AsyncSession, token: str) -> StoryState:
    """Validate a bearer token. Every failure is the same `StoryNotFound`."""
    if not token.startswith(TOKEN_PREFIX) or len(token) > 128:
        raise StoryNotFound("Link unavailable")
    now = int(time.time())
    link = await db.scalar(
        select(NotificationLiveLink).where(NotificationLiveLink.token_hash == hash_token(token))
    )
    if link is None or link.revoked_at is not None or link.expires_at <= now:
        raise StoryNotFound("Link unavailable")
    owner_active = await db.scalar(
        select(User.id).where(User.id == link.user_id, User.is_active.is_(True))
    )
    story = await db.get(NotificationStory, link.story_id)
    state = await latest_state(db, story) if story is not None else None
    if owner_active is None or state is None or state.story.user_id != link.user_id:
        raise StoryNotFound("Link unavailable")
    if link.last_used_at is None or now - link.last_used_at > _LAST_USED_GRANULARITY:
        link.last_used_at = now
    return state


async def link_owner_view(
    db: AsyncSession, user_id: UUID, token: str, *, proxy_prefix: str
) -> dict[str, Any]:
    """A signed-in owner opening their own live link sees the private state; others see 404."""
    state = await resolve_live_link(db, token)
    if state.story.user_id != user_id:
        raise StoryNotFound("Link unavailable")
    return await owner_view(
        db, user_id, state.story.id, proxy_path=f"{proxy_prefix}/{state.story.id}/images"
    )


def public_view(state: StoryState) -> dict[str, Any]:
    """The bearer-token view: no identifiers, no canonical facts."""
    now = int(time.time())
    return public_projection(
        public_title=state.item.public_title,
        public_body=state.item.public_body,
        phase=state.story.phase,
        revision=state.story.revision,
        updated_at=state.story.updated_at,
        expires_at=state.item.expires_at,
        now=now,
    )


# --- plugin actions --------------------------------------------------------------------------


async def _authorised_action(
    db: AsyncSession, state: StoryState, user_id: UUID, action_id: str, *, confirmed: bool
) -> tuple[PluginActionTarget, UUID]:
    """The declared action and its source installation, after every recheck."""
    active = await db.scalar(select(User.id).where(User.id == user_id, User.is_active.is_(True)))
    content = parse_content(state.revision)
    if active is None or content is None:
        raise ActionDenied("Action unavailable")
    action = next((item for item in content.actions if item.id == action_id), None)
    if action is None or not isinstance(action.target, PluginActionTarget):
        raise ActionDenied("Action unavailable")
    if action.style == "danger" and not confirmed:
        raise ActionDenied("This action needs confirmation")
    if state.item.expires_at is not None and state.item.expires_at <= int(time.time()):
        raise ActionDenied("This notification has expired")
    try:
        if state.story.scope_key == HOST_SCOPE:
            raise ValueError("Host stories declare no plugin actions")
        installation_id = UUID(state.story.scope_key)
    except ValueError as exc:
        raise ActionDenied("Action unavailable") from exc
    plugin_id = state.story.source
    registration = await db.scalar(
        select(PluginNotificationTypeRegistration.id).where(
            PluginNotificationTypeRegistration.plugin_id == plugin_id,
            PluginNotificationTypeRegistration.installation_id == installation_id,
            PluginNotificationTypeRegistration.event_type == state.story.event_type,
            PluginNotificationTypeRegistration.revoked_at.is_(None),
        )
    )
    if registration is None or not await has_capability_grant(
        db,
        plugin_id=plugin_id,
        installation_id=installation_id,
        capability=ACTION_CAPABILITY,
        user_id=user_id,
    ):
        raise ActionDenied("The source of this notification is no longer authorised")
    return action.target, installation_id


async def invoke_story_action(
    db: AsyncSession,
    user_id: UUID,
    story_id: UUID,
    action_id: str,
    *,
    confirmed: bool = False,
    runtime: PluginRuntimeClient | None = None,
) -> dict[str, Any]:
    """Run one declared plugin action after every authorisation is rechecked.

    The action and its input come from the stored head revision, never from the request, so a
    caller cannot invoke an undeclared action or substitute inputs. Rechecked now: the actor is
    the story owner and still active, the story is the live head, the source plugin installation
    still has an active registration for the event type, and its current grant covers actions.
    """
    state = await owned_state(db, user_id, story_id)
    target, installation_id = await _authorised_action(
        db, state, user_id, action_id, confirmed=confirmed
    )
    client = runtime or PluginRuntimeClient()
    try:
        result = await client.notification_story_action(
            state.story.source,
            target.action_id,
            dict(target.input),
            user_id=str(user_id),
            installation_id=str(installation_id),
            story_id=str(story_id),
            revision=state.story.revision,
        )
    except (PluginRuntimeRequestError, PluginRuntimeUnavailable) as exc:
        raise ActionUnavailable("The source could not complete this action") from exc
    message = result.get("message") if isinstance(result, dict) else None
    return {
        "ok": True,
        "message": message[:300] if isinstance(message, str) and message.isprintable() else None,
    }


async def contribution_authorised(
    db: AsyncSession,
    contribution: RendererContribution,
    *,
    plugin_id: str,
    installation_id: UUID,
    user_id: UUID,
) -> bool:
    """Whether a producer may present its own event type with this template.

    The type must be an active registration of this installation and the installation needs its
    emit grant; HTML templates additionally need the explicit high-risk `notifications.sensitive`
    grant (until #115 defines a dedicated one). Contributions never reach PUBLIC projections.
    """
    registered = await db.scalar(
        select(PluginNotificationTypeRegistration.id).where(
            PluginNotificationTypeRegistration.plugin_id == plugin_id,
            PluginNotificationTypeRegistration.installation_id == installation_id,
            PluginNotificationTypeRegistration.event_type == contribution.event_type,
            PluginNotificationTypeRegistration.revoked_at.is_(None),
        )
    )
    required = [ACTION_CAPABILITY]
    if contribution.kind == "html_template":
        required.append("notifications.sensitive")
    if registered is None:
        return False
    for capability in required:
        if not await has_capability_grant(
            db,
            plugin_id=plugin_id,
            installation_id=installation_id,
            capability=capability,
            user_id=user_id,
        ):
            return False
    return True
