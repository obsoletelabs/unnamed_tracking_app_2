"""Bounded pre-account recipients and host-owned single-use invitation credentials."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class NotificationPendingRecipient(Base):
    __tablename__ = "notification_pending_recipients"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    retired_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class UserInvitation(Base):
    __tablename__ = "user_invitations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    pending_recipient_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_pending_recipients.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    issued_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    flow_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # Address is encrypted; the digest permits bounded duplicate/rate checks.
    email_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    encrypted_email: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    notification_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
