"""Payload-free recipient dedupe identities survive deletion and content retention."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class NotificationReceipt(Base):
    __tablename__ = "notification_receipts"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notification_receipt_user_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, server_default="0"
    )
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
