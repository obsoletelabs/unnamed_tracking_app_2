"""Durable user decisions to retain two independently owned game entries."""

from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class GameDuplicateDismissal(Base):
    __tablename__ = "game_duplicate_dismissals"
    __table_args__ = (CheckConstraint("first_game_id < second_game_id", name="ordered_pair"),)

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    first_game_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    second_game_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
