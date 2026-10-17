"""Column helpers for rows that belong to a notification destination."""

from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import MappedColumn, mapped_column


def destination_fk() -> MappedColumn[UUID]:
    """Required foreign key to a notification destination, removed with it."""
    return mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_destinations.id", ondelete="CASCADE"),
        nullable=False,
    )
