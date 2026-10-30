"""Grant-bound Playnite receivers and one-use remote launch requests."""

from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class RemoteLaunchDevice(Base):
    __tablename__ = "remote_launch_devices"

    # A renewed consent creates a different receiver; device labels are never identity proof.
    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey("delegated_grants.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RemoteLaunchRequest(Base):
    __tablename__ = "remote_launch_requests"
    __table_args__ = (
        UniqueConstraint("user_id", "request_key", name="uq_remote_launch_requests_user_key"),
        CheckConstraint(
            "state IN ('pending', 'dispatched', 'accepted', 'failed', 'expired', 'unknown')",
            name="ck_remote_launch_requests_state",
        ),
        Index("ix_remote_launch_requests_user_created", "user_id", "created_at"),
        Index("ix_remote_launch_requests_device_state", "device_id", "state"),
        Index("ix_remote_launch_requests_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("remote_launch_devices.grant_id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[UUID] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True
    )
    playnite_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Caller-generated request UUID makes a retried browser POST idempotent.
    request_key: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    failure_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dispatched_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
