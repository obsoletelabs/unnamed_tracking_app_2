"""Record producer urgency requests and the timing reason of held deliveries.

Revision ID: a3c81e5d7f20
Revises: 61de2c6d5459
Create Date: 2026-10-17 06:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

revision: str = "a3c81e5d7f20"
down_revision: Union[str, None] = "61de2c6d5459"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive and defaulted: every existing notification keeps requesting normal urgency and every
    # queued delivery keeps its schedule. User preferences live in the existing JSON document, where
    # a missing `notification_timing` key means the legacy behaviour, so no preference row changes.
    h.add_column_if_missing(
        "notifications",
        sa.Column(
            "requested_urgency", sa.String(length=16), server_default="normal", nullable=False
        ),
    )
    h.add_column_if_missing(
        "notification_deliveries", sa.Column("timing_reason", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    for table, column in (
        ("notification_deliveries", "timing_reason"),
        ("notifications", "requested_urgency"),
    ):
        if h.has_column(table, column):
            op.drop_column(table, column)
