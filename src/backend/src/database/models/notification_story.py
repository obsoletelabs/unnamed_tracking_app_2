"""Durable story identity, revision history and item links for evolving notifications.

Stories hold only lifecycle state and the content of their revisions. Dedupe
receipts live in `notification_receipts` and keep their own lifetime, so
deleting content never reopens an event for replay.
"""

import time
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base

HOST_SCOPE = "host"


class NotificationStory(Base):
    __tablename__ = "notification_stories"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "event_type", "scope_key", "story_key", name="uq_notification_story_identity"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # The source installation id, or "host": a reinstalled plugin gets new stories.
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    story_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=lambda: int(time.time())
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=lambda: int(time.time())
    )


class NotificationStoryRevision(Base):
    __tablename__ = "notification_story_revisions"
    __table_args__ = (
        UniqueConstraint("story_id", "revision", name="uq_notification_story_revision"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    story_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(64), nullable=False)
    # Item that presented this revision; kept (not cascaded) so history survives item changes.
    notification_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True
    )
    # "create_story" | "update_in_place" | "new_linked_item"
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    receipt_key: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=lambda: int(time.time())
    )
    # Content of this revision; erased when the presenting item is deleted.
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Canonical PRIVATE content document (`NotificationContent`); erased with the presenting item.
    content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class NotificationLiveLink(Base):
    """Unguessable, expiring, revocable capability for the public projection of one story.

    Only the SHA-256 of the random token is stored, so the table cannot reproduce a link. The
    token carries no identifiers; the story and owner are looked up from this row.
    """

    __tablename__ = "notification_live_links"
    __table_args__ = (Index("ix_notification_live_links_story", "story_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    story_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=lambda: int(time.time())
    )
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
