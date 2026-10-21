"""keep provider-reported custom fields on games

Revision ID: a9c41d7e2b53
Revises: 5eef5ee3de75
Create Date: 2026-10-21 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "a9c41d7e2b53"
down_revision: Union[str, None] = "5eef5ee3de75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows stay null: nothing was reported before this column existed.
    h.add_column_if_missing(
        "games",
        sa.Column("provider_custom", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    if h.has_column("games", "provider_custom"):
        op.drop_column("games", "provider_custom")
