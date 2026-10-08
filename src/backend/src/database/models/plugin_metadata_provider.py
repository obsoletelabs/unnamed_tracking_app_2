"""Durable provider declarations and encrypted, independently scoped configuration."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class PluginMetadataProviderRegistration(Base):
    """Provider identity remains separate from the current plugin installation."""

    __tablename__ = "plugin_metadata_provider_registrations"

    provider_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    installation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    declaration: Mapped[dict] = mapped_column(JSONB, nullable=False)
    registered_at: Mapped[int] = mapped_column(BigInteger, default=unix_timestamp, nullable=False)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger)


class PluginProviderConfiguration(Base):
    """A system configuration cannot be overwritten by a user's personal configuration."""

    __tablename__ = "plugin_provider_configurations"
    __table_args__ = (
        UniqueConstraint("provider_id", "scope", name="uq_plugin_provider_configuration_scope"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # "system" or an authenticated user's UUID; never supplied by a plugin.
    scope: Mapped[str] = mapped_column(String(36), nullable=False)
    encrypted_values: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=unix_timestamp, nullable=False)
