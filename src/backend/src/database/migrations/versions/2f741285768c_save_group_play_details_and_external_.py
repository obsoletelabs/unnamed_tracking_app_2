"""save group play details and external avatars

Revision ID: 2f741285768c
Revises: 3acc697f5448
Create Date: 2026-10-13 15:06:44.465037

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "2f741285768c"
down_revision: Union[str, None] = "3acc697f5448"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adoption replays migrations on existing schemas; preserve existing data.
    h.add_column_if_missing(
        "games",
        sa.Column("play_compatibility", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    h.add_column_if_missing(
        "public_app_actors", sa.Column("avatar_url", sa.String(length=2048), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("public_app_actors", "avatar_url")
    op.drop_column("games", "play_compatibility")
