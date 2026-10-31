"""keep catalogue details provided by metadata providers on achievements

Revision ID: b7e3d92a41c8
Revises: a9c41d7e2b53
Create Date: 2026-10-21 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "b7e3d92a41c8"
down_revision: Union[str, None] = "a9c41d7e2b53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> tuple[sa.Column, ...]:
    # New Column objects on every call, since a Column can be attached to only one table.
    return (
        sa.Column("medal", sa.String(length=10), nullable=True),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("locked_icon_url", sa.Text(), nullable=True),
        sa.Column("source_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_provider", sa.String(length=128), nullable=True),
    )


def upgrade() -> None:
    # Existing rows stay null: every stored achievement so far came from a platform sync.
    for column in _columns():
        h.add_column_if_missing("achievements", column)


def downgrade() -> None:
    # Catalogue rows only exist because of this revision, so they go with it.
    op.execute("DELETE FROM achievements WHERE provider = 'catalog'")
    for column in reversed(_columns()):
        if h.has_column("achievements", column.name):
            op.drop_column("achievements", column.name)
