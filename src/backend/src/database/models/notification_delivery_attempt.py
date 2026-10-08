"""Core-owned physical attempts, separate from the logical delivery obligation."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class NotificationDeliveryAttempt(Base):
    __tablename__ = "notification_delivery_attempts"
    __table_args__ = (
        UniqueConstraint("delivery_id", "number", name="uq_notification_attempt_number"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    delivery_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_token: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    started_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finished_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
