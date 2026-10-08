"""Concrete notification endpoints; trust is never inferred from a provider."""

import time
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class NotificationDestination(Base):
    __tablename__ = "notification_destinations"
    __table_args__ = (
        UniqueConstraint("user_id", "provider_id", "endpoint_key", name="uq_notification_endpoint"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint_key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_context: Mapped[str] = mapped_column(String(16), nullable=False, default="external")
    # 0 PUBLIC, 1 PRIVATE. External SECURE eligibility also requires current host proof.
    privacy: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    verified_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    installation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Opaque host-owned reference, never an address/secret returned to a renderer.
    configuration_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Host-only encrypted endpoint values; plugins and routing DTOs never receive these.
    encrypted_configuration: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recovery_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    media_consent_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_consent_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=lambda: int(time.time()))
