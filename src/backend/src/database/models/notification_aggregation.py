"""Durable aggregation windows: pending batches survive restarts and worker overlap."""

import time
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class NotificationAggregationWindow(Base):
    """One open or sealed grouping window for one destination revision and scope."""

    __tablename__ = "notification_aggregation_windows"
    __table_args__ = (
        # At most one accepting window per destination revision, strategy and scope.
        Index(
            "uq_notification_window_open",
            "destination_id",
            "destination_revision",
            "strategy",
            "scope_key",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_notification_window_due", "status", "window_end"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_destinations.id", ondelete="CASCADE"),
        nullable=False,
    )
    destination_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(200), nullable=False)
    # open: accepting members; full: closed early at the member cap; sealed: batches planned.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    window_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    window_end: Mapped[int] = mapped_column(BigInteger, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sealed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=lambda: int(time.time())
    )
