"""Progress of the resumable library backfill into the game identity model (#102).

One row per user records how far the backfill got, what it did and what went
wrong, so an interrupted run resumes and a finished run can be audited. It holds
counts and error codes only, never titles or other library content.
"""

from enum import StrEnum
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class BackfillStatus(StrEnum):
    """Closed set; `incomplete` means a rerun is needed, `failed` that a stage raised."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class GameIdentityBackfill(Base):
    __tablename__ = "game_identity_backfills"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'incomplete', 'failed')",
            name="ck_game_identity_backfills_status",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BackfillStatus.PENDING.value, server_default="pending"
    )
    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completed_stages: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    before_counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    after_counts: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    errors: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    finished_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )
