"""External app principals and host-held connections, separate from application accounts."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class PublicAppActor(Base):
    __tablename__ = "public_app_actors"
    __table_args__ = (UniqueConstraint("installation_id", "subject"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    installation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider_installation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    account_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class PublicAppSession(Base):
    __tablename__ = "public_app_sessions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("public_app_actors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    room: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    verified_at: Mapped[int] = mapped_column(BigInteger, default=unix_timestamp, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class PublicAppSource(Base):
    __tablename__ = "public_app_sources"
    __table_args__ = (UniqueConstraint("actor_id", "origin"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("public_app_actors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    origin: Mapped[str] = mapped_column(String(512), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    credential_kind: Mapped[str] = mapped_column(
        String(16), default="delegated", server_default="delegated", nullable=False
    )
    remote_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    last_checked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    library: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
