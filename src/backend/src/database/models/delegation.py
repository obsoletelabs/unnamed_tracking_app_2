"""Host-owned consent, revocable integration credentials and content-free audit records."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class IntegrationPolicy(Base):
    __tablename__ = "integration_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    instance_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), default=uuid4, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_guests: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_any_origin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    allow_legacy_api_keys: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    guest_retention_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    maximum_grant_hours: Mapped[int] = mapped_column(Integer, default=336, nullable=False)


class IntegrationClient(Base):
    __tablename__ = "integration_clients"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    origin: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, default=unix_timestamp, nullable=False)


class DelegationRequest(Base):
    __tablename__ = "delegation_requests"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("integration_clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=unix_timestamp, nullable=False)


class DelegatedGrant(Base):
    __tablename__ = "delegated_grants"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("integration_clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("delegation_requests.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=unix_timestamp, nullable=False)


class DelegationAudit(Base):
    __tablename__ = "delegation_audit"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("integration_clients.id", ondelete="SET NULL"), nullable=True
    )
    grant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("delegated_grants.id", ondelete="SET NULL"), nullable=True
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, default=unix_timestamp, nullable=False)
