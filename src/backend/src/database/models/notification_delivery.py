"""Core-owned notification delivery state for built-in and plugin providers."""

from __future__ import annotations

import time
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base
from src.database.models import notification_aggregation  # noqa: F401  # pylint: disable=unused-import

# Statuses that still owe work: held in a window, queued, leased or waiting to retry.
ACTIVE_DELIVERY_STATUSES = ("pending", "processing", "retry_wait", "aggregating")


class NotificationDelivery(Base):
    requested_urgency: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal", server_default="normal"
    )
    effective_urgency: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal", server_default="normal"
    )
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "notification_id",
            "destination_id",
            name="uq_notification_delivery_notification_destination",
        ),
        Index("ix_notification_delivery_pending", "status", "next_attempt_at"),
        Index("ix_notification_delivery_window", "window_id"),
        Index("ix_notification_delivery_batch", "batch_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    notification_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    destination_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("notification_destinations.id"), nullable=True
    )
    destination_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    projection: Mapped[str] = mapped_column(String(32), nullable=False, default="canonical")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    claim_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lease_until: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Aggregation: a held delivery belongs to a window until it is sealed into a batch. The
    # batch lead (index 0) is the only member that is claimed; it settles its members.
    window_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "notification_aggregation_windows.id",
            ondelete="SET NULL",
            name="fk_notification_delivery_window",
        ),
        nullable=True,
    )
    batch_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    batch_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    next_attempt_at: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=lambda: int(time.time()),
    )
