"""Retained namespaced plugin notification types, independent of inbox content."""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class PluginNotificationTypeRegistration(Base):
    __tablename__ = "plugin_notification_type_registrations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    installation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    registered_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
