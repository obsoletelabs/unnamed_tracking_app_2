"""Owner-scoped subscription snapshots and local overlays, separate from manual events."""

import time
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
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


class CalendarSource(Base):
    """A secret URL and refresh state; responses expose only its hostname."""

    __tablename__ = "calendar_sources"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_calendar_source_owner"),
        Index("ix_calendar_sources_user", "user_id"),
        Index("ix_calendar_sources_due", "enabled", "next_refresh_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, default="#6b9bd2")
    url_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    hostname: Mapped[str] = mapped_column(String(253), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    refresh_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=360)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    etag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_attempt_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_success_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_manual_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    next_refresh_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_status: Mapped[str] = mapped_column(String(32), nullable=False, default="never")
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=lambda: int(time.time())
    )


class CalendarSourceEvent(Base):
    """A complete successfully parsed upstream component, including removal tombstones."""

    __tablename__ = "calendar_source_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "user_id"],
            ["calendar_sources.id", "calendar_sources.user_id"],
            ondelete="CASCADE",
            name="fk_calendar_source_event_owner",
        ),
        UniqueConstraint("source_id", "uid", "recurrence_id", name="uq_calendar_source_component"),
        Index("ix_calendar_source_events_owner", "user_id", "source_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    uid: Mapped[str] = mapped_column(String(512), nullable=False)
    # Empty for the master; a canonical date/floating/UTC key for an exception.
    recurrence_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    removed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_seen_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class CalendarSourceOverride(Base):
    """A field overlay; never rewrite the original component or the manual calendar."""

    __tablename__ = "calendar_source_overrides"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "user_id"],
            ["calendar_sources.id", "calendar_sources.user_id"],
            ondelete="CASCADE",
            name="fk_calendar_source_override_owner",
        ),
        UniqueConstraint("source_id", "uid", "recurrence_id", name="uq_calendar_source_override"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    uid: Mapped[str] = mapped_column(String(512), nullable=False)
    # "*" applies to the series; otherwise the same stable occurrence identity as the feed.
    recurrence_id: Mapped[str] = mapped_column(String(100), nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
