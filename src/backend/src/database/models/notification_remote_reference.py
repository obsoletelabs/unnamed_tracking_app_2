"""Host-held references to the remote message that presented a notification at a destination.

The reference is opaque provider data. It is never returned by any API, never given to plugins
other than the provider that produced it, and it is keyed by destination revision so a changed
destination can never be edited through an older reference.
"""

import time
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base
from src.database.models.destination_scope import destination_fk


class NotificationRemoteReference(Base):
    __tablename__ = "notification_remote_references"
    __table_args__ = (
        UniqueConstraint(
            "notification_id",
            "destination_id",
            "destination_revision",
            name="uq_notification_remote_reference",
        ),
        Index("ix_notification_remote_reference_story", "story_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    notification_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="CASCADE")
    )
    story_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("notification_stories.id", ondelete="CASCADE")
    )
    destination_id: Mapped[UUID] = destination_fk()
    destination_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # Opaque provider message identifier; NULL when the provider cannot address it again.
    remote_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    delivered_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # SHA-256 of the exact rendering last presented; an unchanged rendering needs no edit.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # current | stale (an edit failed; further revisions use follow-ups)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="current")
    # True when the remote message also presents other items (a digest); it cannot be edited.
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    edit_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=lambda: int(time.time())
    )
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=lambda: int(time.time())
    )
