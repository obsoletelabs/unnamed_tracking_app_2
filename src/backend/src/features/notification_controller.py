"""Single transactional boundary from facts/legacy producers to routed notifications."""

import hashlib
import json
import time
from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.user import User
from src.features.notification_audit import record_notices
from src.features.notification_contract import (
    SINGLE_ITEM_PHASE,
    STORY_PURPOSES,
    RevisionDecision,
    single_item_lifecycle,
)
from src.features.notification_plugin_policy import delivery_projection
from src.features.notification_policy import (
    MEDIA_KINDS,
    Trust,
    preference_enabled,
)
from src.features.notification_providers.delivery import ensure_deliveries
from src.features.notification_rich import render_plain
from src.features.notification_safety import UnsafeContentError, validate_rich_content
from src.features.notification_stories import StoryReference, apply_revision
from src.plugin_api.notification_story_contracts import NotificationContent

EVENT_TYPES = {
    "episode_aired": "media.episode.aired",
    "season_started": "media.season.started",
    "sequel_announced": "media.sequel.announced",
    "movie_released": "media.movie.released",
    "franchise_season": "media.franchise.season",
    "franchise_extra": "media.franchise.extra",
    "session_anomaly": "security.session.anomaly",
    "plugin_update": "system.plugin.update",
    "plugin": "plugin.notice",
    "game_released": "game.released",
    "game_sale": "game.sale.started",
    "game_price_hit": "game.price.threshold_hit",
}


async def emit_invitation(db, invitation, endpoint) -> UUID:
    """Host-reserved bootstrap interpretation using the normal durable delivery queue."""
    now = int(time.time())
    notice = Notification(
        id=uuid4(),
        pending_recipient_id=invitation.pending_recipient_id,
        kind="user_invite",
        event_type="auth.user_invite",
        source=invitation.plugin_id,
        source_installation_id=invitation.installation_id,
        media_type="auth_invitation",
        media_id=invitation.id,
        title="You have been invited",
        body="An account invitation was issued.",
        event_at=now,
        dedupe_key=f"invitation:{invitation.id}",
        required_trust=int(Trust.PRIVATE),
        purpose="invitation",
        inbox_visible=False,
        expires_at=invitation.expires_at,
    )
    db.add(notice)
    invitation.notification_id = notice.id
    await db.flush()
    await ensure_deliveries(db, [notice.id])
    delivery = await db.scalar(
        select(NotificationDelivery.id).where(
            NotificationDelivery.notification_id == notice.id,
            NotificationDelivery.destination_id == endpoint.id,
        )
    )
    if delivery is None:
        raise ValueError(
            "Administrative notification policy does not permit invitation email delivery"
        )
    return notice.id


async def emit_recovery_request(db: AsyncSession, token) -> UUID:
    """Reserved interpretation; only final eligible dispatch may resolve the secret."""
    now = int(time.time())
    notice = Notification(
        id=uuid4(),
        user_id=token.user_id,
        kind="password_reset",
        event_type="auth.password_reset",
        source=token.plugin_id,
        source_installation_id=token.installation_id,
        media_type="auth_recovery",
        media_id=token.id,
        title="Reset your password",
        body="A password reset was requested.",
        event_at=now,
        dedupe_key=f"recovery:{token.id}",
        required_trust=int(Trust.SECURE),
        purpose="recovery",
        inbox_visible=False,
        expires_at=token.expires_at,
    )
    db.add(notice)
    await db.flush()
    await ensure_deliveries(db, [notice.id])
    await record_notices(db, [notice], "created")
    return notice.id


async def emit_verification_request(
    db: AsyncSession, destination: NotificationDestination, challenge_id: UUID, expires_at: int
) -> UUID | None:
    """Reserved host interpretation; the code lives encrypted in the proof record only."""
    if destination.user_id is None:
        return None
    user = await db.get(User, destination.user_id)
    if user is None or not user.is_active:
        return None
    now = int(time.time())
    notice = Notification(
        id=uuid4(),
        user_id=destination.user_id,
        kind="destination_verification",
        event_type="destination.verification",
        source="host.verification",
        media_type="notification_destination",
        media_id=destination.id,
        title="Verify your email destination"
        if destination.provider_id == "core.smtp"
        else "Verify your notification destination",
        body="An expiring verification code was requested.",
        event_at=now,
        dedupe_key=f"verification:{challenge_id}",
        required_trust=int(Trust.PRIVATE),
        purpose="verification",
        inbox_visible=False,
        expires_at=expires_at,
    )
    prefs = await load_preferences(db, destination.user_id)
    if not await delivery_projection(db, notice, destination, prefs):
        return None
    db.add(notice)
    db.add(
        NotificationReceipt(
            user_id=destination.user_id,
            dedupe_key=notice.dedupe_key,
            event_type=notice.event_type,
            source=notice.source,
            occurred_at=now,
        )
    )
    await db.flush()
    await ensure_deliveries(db, [notice.id], preferences=prefs)
    return notice.id


@dataclass(frozen=True)
class NotificationDraft:  # pylint: disable=too-many-instance-attributes
    """Host-enriched interpretation, distinct from the producer's event facts."""

    kind: str
    user_id: UUID
    entity_type: str
    entity_id: UUID
    occurred_at: int
    dedupe_key: str
    title: str
    body: str
    source: str = "host"
    poster_url: str | None = None
    public_title: str | None = None
    public_body: str | None = None
    group_key: str | None = None
    fingerprint: str | None = None
    source_installation_id: UUID | None = None
    story: StoryReference | None = None
    # A producer's request only; timing policy (#108) decides whether it bypasses any delay.
    requested_urgency: Literal["normal", "critical"] = "normal"
    # Validated canonical rich content (#109). When present, title and body are derived from it.
    content: NotificationContent | None = None


@dataclass(frozen=True)
class NotificationEvent:
    """A producer identifies what happened; the host chooses text and routing."""

    type: str
    user_id: UUID
    entity_type: str
    entity_id: UUID
    occurred_at: int
    dedupe_key: str
    data: dict[str, Any]


@dataclass(frozen=True)
class NotificationInterpretation:
    """Host-validated source policy; producers cannot pass it through legacy send APIs."""

    event_type: str
    required_trust: Trust
    purpose: str
    severity: str
    installation_id: UUID


async def emit_interpreted(
    db: AsyncSession, draft: NotificationDraft, interpretation: NotificationInterpretation
) -> UUID | None:
    """Registered plugin source boundary; authorization precedes host interpretation."""
    if draft.kind != "plugin" or not interpretation.event_type.startswith(f"{draft.source}."):
        raise ValueError("Registered notification source identity is invalid")
    return await _accept(db, draft, interpretation)


async def emit(db: AsyncSession, event: NotificationEvent) -> UUID | None:
    """Resolve a registered host handler; plugin facts use the source gateway boundary."""
    # The interpreter returns this module's draft/event types; import after initialization.
    # pylint: disable-next=import-outside-toplevel
    from src.features.game_notifications import interpret_game_event

    if event.type not in {"game.released", "game.sale.started", "game.price.threshold_hit"}:
        raise ValueError("Unknown notification event type")
    draft = await interpret_game_event(db, event)
    return await _accept(db, draft) if draft else None


async def _accept(
    db: AsyncSession,
    event: NotificationDraft,
    interpretation: NotificationInterpretation | None = None,
) -> UUID | None:
    """Flush atomically; the caller owns commit and no provider is contacted here."""
    if event.kind not in EVENT_TYPES:
        raise ValueError("Unknown host notification type")
    content = _accepted_content(event, interpretation)
    if content is None and event.content is not None:
        event = replace(event, content=None)
    if content is not None:
        event = _with_content(event, content, interpretation)
    if (
        len(event.dedupe_key) > 200
        or len(event.source) > 128
        or len(event.title) > 500
        or len(event.body) > 10000
        or len(event.entity_type) > 32
    ):
        raise ValueError("Notification event exceeds its bounds")
    if not event.dedupe_key or event.occurred_at < 0:
        raise ValueError("Notification event requires an identity and a Unix timestamp")
    user_exists = await db.scalar(
        select(User.id).where(User.id == event.user_id, User.is_active.is_(True))
    )
    if user_exists is None:
        return None
    security = event.kind == "session_anomaly"
    purpose = interpretation.purpose if interpretation else "security" if security else "standard"
    lifetime = 600 if purpose == "recovery" else 86400 if purpose == "security" else 30 * 86400
    notification = Notification(
        user_id=event.user_id,
        kind=event.kind,
        event_type=interpretation.event_type if interpretation else EVENT_TYPES[event.kind],
        source=event.source,
        source_installation_id=interpretation.installation_id
        if interpretation
        else event.source_installation_id,
        media_type=event.entity_type,
        media_id=event.entity_id,
        title=event.title,
        body=event.body,
        poster_url=event.poster_url,
        event_at=event.occurred_at,
        dedupe_key=event.dedupe_key,
        required_trust=int(
            interpretation.required_trust
            if interpretation
            else Trust.SECURE
            if security
            else Trust.PRIVATE
        ),
        purpose=purpose,
        severity=interpretation.severity if interpretation else "warning" if security else "info",
        group_key=event.group_key,
        requested_urgency=event.requested_urgency,
        expires_at=min(
            event.occurred_at + lifetime,
            content.expires_at if content and content.expires_at else event.occurred_at + lifetime,
        ),
        public_title=event.public_title if event.kind in MEDIA_KINDS else None,
        public_body=event.public_body if event.kind in MEDIA_KINDS else None,
        inbox_visible=False,
    )
    preferences = await load_preferences(db, event.user_id)
    if not preference_enabled(notification, preferences):
        return None
    if event.story is None:
        if not await _record_receipt(db, event, notification):
            return None
        db.add(notification)
        await db.flush()
        await record_notices(db, [notification], "created")
        await ensure_deliveries(db, [notification.id], preferences=preferences)
        return notification.id
    if notification.purpose not in STORY_PURPOSES:
        raise ValueError("Only standard and security notifications can join stories")
    # A rejected transition rolls the receipt back with it, so it can be retried or corrected.
    async with db.begin_nested():
        if not await _record_receipt(db, event, notification):
            return None
        outcome = await apply_revision(
            db,
            notification,
            event.story,
            preferences=preferences,
            content=content.model_dump(mode="json") if content else None,
        )
    if outcome.decision != RevisionDecision.UPDATE_IN_PLACE:
        await record_notices(db, [outcome.item], "created")
    # Idempotent: an in-place revision only adds work for destinations that have none yet.
    await ensure_deliveries(db, [outcome.item.id], preferences=preferences)
    return outcome.item.id


def _accepted_content(
    event: NotificationDraft, interpretation: NotificationInterpretation | None
) -> NotificationContent | None:
    """Validated rich content. A plugin's unsafe content is rejected to its producer; the host's
    own producers describe the user's library (a game titled "Secret: Hidden Tales" looks like a
    credential to the scanner), so their notification degrades to its plain text instead of
    failing every inbox refresh that generates it."""
    if event.content is None:
        return None
    try:
        return validate_rich_content(event.content)
    except UnsafeContentError:
        if interpretation is not None:
            raise
        return None


def _with_content(
    event: NotificationDraft,
    content: NotificationContent,
    interpretation: NotificationInterpretation | None,
) -> NotificationDraft:
    """Title and body for legacy surfaces come from the content; plain events become one-item
    stories so a live page and revisions exist for them."""
    if event.kind == "session_anomaly" and interpretation is None:
        purpose = "security"
    else:
        purpose = interpretation.purpose if interpretation else "standard"
    if purpose not in STORY_PURPOSES:
        raise ValueError("Only standard and security notifications can carry rich content")
    event_type = interpretation.event_type if interpretation else EVENT_TYPES[event.kind]
    story = event.story or StoryReference(
        key=event.dedupe_key[:128],
        phase=SINGLE_ITEM_PHASE,
        lifecycle=single_item_lifecycle(event_type),
    )
    return replace(
        event,
        title=content.title,
        body=render_plain(content)[:10000],
        story=story,
    )


def _story_fingerprint(event: NotificationDraft) -> str | None:
    """Replay identity of a story event: key, phase and text, never the occurrence time."""
    if event.story is None:
        return event.fingerprint
    facts = [event.story.key, event.story.phase, event.title, event.body]
    if event.content is not None:
        facts.append(event.content.model_dump_json())
    digest = hashlib.sha256(json.dumps(facts, separators=(",", ":")).encode()).hexdigest()
    return event.fingerprint or digest


async def _record_receipt(
    db: AsyncSession, event: NotificationDraft, notification: Notification
) -> bool:
    """Insert the payload-free receipt; False means an identical replay (no work)."""
    fingerprint = _story_fingerprint(event)
    receipt_id = await db.scalar(
        pg_insert(NotificationReceipt)
        .values(
            user_id=event.user_id,
            dedupe_key=event.dedupe_key,
            event_type=notification.event_type,
            source=event.source,
            occurred_at=event.occurred_at,
            fingerprint=fingerprint,
            story_key=event.story.key if event.story else None,
            story_phase=event.story.phase if event.story else None,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
        .returning(NotificationReceipt.id)
    )
    if receipt_id is not None:
        return True
    existing = await db.scalar(
        select(NotificationReceipt).where(
            NotificationReceipt.user_id == event.user_id,
            NotificationReceipt.dedupe_key == event.dedupe_key,
        )
    )
    if (
        existing is not None
        and fingerprint is not None
        and (existing.fingerprint is not None and existing.fingerprint != fingerprint)
    ):
        raise ValueError("Event identity was replayed with conflicting facts")
    return False


async def emit_legacy_rows(
    db: AsyncSession,
    user_id: UUID,
    rows: list[dict[str, Any]],
    *,
    source: str = "host",
    source_installation_id: UUID | None = None,
) -> list[UUID]:
    """Compatibility for existing host enrichment; never a plugin-selected type/policy."""
    created = []
    for row in rows:
        event = NotificationDraft(
            kind=row["kind"],
            user_id=user_id,
            entity_type=row["media_type"],
            entity_id=row["media_id"],
            occurred_at=row["event_at"],
            dedupe_key=row["dedupe_key"],
            title=row["title"],
            body=row["body"],
            poster_url=row.get("poster_url"),
            source=source,
            source_installation_id=source_installation_id,
            # These four legacy media handlers only contain public release facts.
            # Custom plugin text is never eligible for this projection.
            public_title=row["title"] if row["kind"] in MEDIA_KINDS and source == "host" else None,
            public_body=row["body"] if row["kind"] in MEDIA_KINDS and source == "host" else None,
            group_key=f"{row['kind']}:{row['media_id']}",
            story=row.get("story"),
        )
        notification_id = await _accept(db, event)
        if notification_id is not None:
            created.append(notification_id)
    return created
