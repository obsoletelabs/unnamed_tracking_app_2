"""Host-owned, expiring proof challenges for a concrete endpoint revision."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class NotificationVerification(Base):
    __tablename__ = "notification_verifications"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    destination_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_destinations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    destination_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    notification_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="SET NULL"),
        nullable=True,
    )
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=unix_timestamp, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
