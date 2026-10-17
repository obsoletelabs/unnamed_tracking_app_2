"""Columns shared by rows that are bound to one revision of a notification destination."""

from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column


def destination_fk() -> MappedColumn[UUID]:
    """Required foreign key to a notification destination, removed with it."""
    return mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_destinations.id", ondelete="CASCADE"),
        nullable=False,
    )


class DestinationRevisionMixin:
    """The destination revision a row was written against and the provider that owns it."""

    destination_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
