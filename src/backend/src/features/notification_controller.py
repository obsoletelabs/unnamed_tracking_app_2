"""Single transactional boundary from facts/legacy producers to routed notifications."""

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.preferences import load_preferences
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.user import User
from src.features.notification_audit import record_notices
from src.features.notification_policy import (
    MEDIA_KINDS,
    Trust,
    preference_enabled,
    select_projection,
)
from src.features.notification_providers.delivery import ensure_deliveries

EVENT_TYPES = {
    "episode_aired": "media.episode.aired",
    "season_started": "media.season.started",
    "sequel_announced": "media.sequel.announced",
    "movie_released": "media.movie.released",
    "session_anomaly": "security.session.anomaly",
    "plugin_update": "system.plugin.update",
    "plugin": "plugin.notice",
    "game_released": "game.released",
    "game_sale": "game.sale.started",
    "game_price_hit": "game.price.threshold_hit",
}


async def emit_verification_request(
    db: AsyncSession, destination: NotificationDestination, challenge_id: UUID, expires_at: int
) -> UUID | None:
    """Reserved host interpretation; the code lives encrypted in the proof record only."""
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
        title="Verify your email destination",
        body="An expiring verification code was requested.",
        event_at=now,
        dedupe_key=f"verification:{challenge_id}",
        required_trust=int(Trust.PRIVATE),
        purpose="verification",
        inbox_visible=False,
        expires_at=expires_at,
    )
    prefs = await load_preferences(db, destination.user_id)
    if not select_projection(notice, destination, prefs):
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
        expires_at=event.occurred_at + lifetime,
        public_title=event.public_title if event.kind in MEDIA_KINDS else None,
        public_body=event.public_body if event.kind in MEDIA_KINDS else None,
        inbox_visible=False,
    )
    preferences = await load_preferences(db, event.user_id)
    if not preference_enabled(notification, preferences):
        return None
    receipt_id = await db.scalar(
        pg_insert(NotificationReceipt)
        .values(
            user_id=event.user_id,
            dedupe_key=event.dedupe_key,
            event_type=notification.event_type,
            source=event.source,
            occurred_at=event.occurred_at,
            fingerprint=event.fingerprint,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "dedupe_key"])
        .returning(NotificationReceipt.id)
    )
    if receipt_id is None:
        existing = await db.scalar(
            select(NotificationReceipt).where(
                NotificationReceipt.user_id == event.user_id,
                NotificationReceipt.dedupe_key == event.dedupe_key,
            )
        )
        if (
            existing is not None
            and event.fingerprint is not None
            and (existing.fingerprint is not None and existing.fingerprint != event.fingerprint)
        ):
            raise ValueError("Event identity was replayed with conflicting facts")
        return None
    db.add(notification)
    await db.flush()
    await record_notices(db, [notification], "created")
    await ensure_deliveries(db, [notification.id], preferences=preferences)
    return notification.id


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
        )
        notification_id = await _accept(db, event)
        if notification_id is not None:
            created.append(notification_id)
    return created
