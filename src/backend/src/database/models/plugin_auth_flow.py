"""Host-owned auth contribution authority and expiring recovery credentials."""

from uuid import UUID, uuid4

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class PluginAuthFlowRegistration(Base):
    __tablename__ = "plugin_auth_flows"

    mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    registered_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AuthRecoveryToken(Base):
    __tablename__ = "auth_recovery_tokens"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    flow_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A changed password, address or identity invalidates previously issued recovery.
    account_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    notification_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AuthRequestLimit(Base):
    """Shared atomic limits; identifiers/IP addresses are keyed digests, not stored text."""

    __tablename__ = "auth_request_limits"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
