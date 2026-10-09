"""In-app notifications: an episode aired, a season started airing, a
sequel was announced for something you finished, a movie came out. Rows
are created from real data (exact air times), never from estimates, and
deduplicated by `dedupe_key` so re-checking never notifies twice."""

import time
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
        Index("ix_notifications_user_event", "user_id", "event_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # "episode_aired" | "season_started" | "sequel_announced" | "movie_released"
    kind: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    media_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    poster_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # when the thing actually happened (exact air time), not when we noticed
    event_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    read_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, default="legacy")
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="host")
    source_installation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    required_trust: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    group_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    inbox_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dismissed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Approved public facts are rendered by the core, never by redacting in a plugin.
    public_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    public_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=lambda: int(time.time())
    )
