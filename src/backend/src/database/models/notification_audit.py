"""Content-free lifecycle outbox and replay history, independent of inbox deletion."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class _LifecycleMetadata:
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Deliberately no content/destination FK: history survives their removal.
    notification_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    delivery_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)


class NotificationLifecycleOutbox(_LifecycleMetadata, Base):
    __tablename__ = "notification_lifecycle_outbox"
    __table_args__ = (Index("ix_notification_outbox_position", "position"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Allocation order preserves changes within a transaction. It is never a
    # replay cursor: late commits receive a new published stream sequence.
    position: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)


class NotificationLifecycleAudit(_LifecycleMetadata, Base):
    __tablename__ = "notification_lifecycle_audit"
    __table_args__ = (
        Index("ix_notification_audit_scope", "user_id", "installation_id", "sequence"),
        Index("ix_notification_audit_retention", "published_at", "sequence"),
    )

    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, unique=True)
    published_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class NotificationLifecycleStream(Base):
    """A transactional counter, not an allocation sequence with late-commit holes."""

    __tablename__ = "notification_lifecycle_stream"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    published_through: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    expired_through: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
