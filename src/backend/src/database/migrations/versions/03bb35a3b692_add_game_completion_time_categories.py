"""add game completion time categories

Revision ID: 03bb35a3b692
Revises: fed12ab64423
Create Date: 2026-10-15 04:11:03.158674

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "03bb35a3b692"
down_revision: Union[str, None] = "fed12ab64423"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing Main story and personal playtime are untouched. Adopted columns
    # retain their values when installations replay the linear migration history.
    for name in (
        "time_to_beat_extras_hours",
        "time_to_beat_completionist_hours",
        "time_to_beat_all_hours",
    ):
        h.add_column_if_missing("games", sa.Column(name, sa.Numeric(6, 1), nullable=True))


def downgrade() -> None:
    for name in (
        "time_to_beat_all_hours",
        "time_to_beat_completionist_hours",
        "time_to_beat_extras_hours",
    ):
        if h.has_column("games", name):
            op.drop_column("games", name)
