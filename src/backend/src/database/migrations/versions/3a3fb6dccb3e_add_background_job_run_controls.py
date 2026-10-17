"""add background job run controls

Revision ID: 3a3fb6dccb3e
Revises: a55b7c1d9e20
Create Date: 2026-10-16 22:20:16.392286

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "3a3fb6dccb3e"
down_revision: Union[str, None] = "a55b7c1d9e20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    sa.Column("paused", sa.Boolean(), server_default="false", nullable=False),
    sa.Column("cancel_requested", sa.Boolean(), server_default="false", nullable=False),
    sa.Column("status", sa.String(length=16), server_default="idle", nullable=False),
    sa.Column("last_success_at", sa.BigInteger(), nullable=True),
    sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
    sa.Column("retry_at", sa.BigInteger(), nullable=True),
    sa.Column("lease_token", sa.String(length=32), nullable=True),
    sa.Column("lease_expires_at", sa.BigInteger(), nullable=True),
)


def upgrade() -> None:
    # Existing schedules keep their switch, interval and history; new columns take
    # neutral defaults (not paused, idle, no lease), so the upgrade is safe to repeat.
    for column in COLUMNS:
        h.add_column_if_missing(
            "job_settings",
            sa.Column(
                column.name,
                column.type,
                server_default=column.server_default,
                nullable=column.nullable,
            ),
        )


def downgrade() -> None:
    for column in reversed(COLUMNS):
        if h.has_column("job_settings", column.name):
            op.drop_column("job_settings", column.name)
