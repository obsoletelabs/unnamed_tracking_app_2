"""Append-only record of administrative account actions; never holds secrets."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class AdminUserAudit(Base):
    """One administrator action on an account or invitation.

    Actor and target are deliberately plain identifiers (no foreign keys) with a
    label snapshot, so the history survives later account deletion.
    """

    __tablename__ = "admin_user_audit"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    actor_username: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    target_label: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    occurred_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
