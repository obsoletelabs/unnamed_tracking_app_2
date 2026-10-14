"""preserve original uploaded file names

Revision ID: fed12ab64423
Revises: 0e3353138981
Create Date: 2026-10-14 22:44:27.973987

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "fed12ab64423"
down_revision: Union[str, None] = "0e3353138981"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows stay nullable; their original names cannot be reconstructed.
    for table in ("game_file_items", "inbox_items", "media_items"):
        h.add_column_if_missing(
            table, sa.Column("original_filename", sa.String(500), nullable=True)
        )


def downgrade() -> None:
    for table in ("media_items", "inbox_items", "game_file_items"):
        if h.has_column(table, "original_filename"):
            op.drop_column(table, "original_filename")
