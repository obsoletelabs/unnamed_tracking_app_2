"""Durable pending work for per-game achievement refreshes."""

from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class AchievementSyncQueue(Base):
    __tablename__ = "achievement_sync_queue"
    __table_args__ = (Index("ix_achievement_sync_queue_user_queued", "user_id", "queued_at"),)

    game_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("games.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    queued_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
