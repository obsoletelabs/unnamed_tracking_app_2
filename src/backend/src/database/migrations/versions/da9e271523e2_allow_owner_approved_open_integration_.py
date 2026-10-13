"""allow owner approved open integration registration

Revision ID: da9e271523e2
Revises: 2f741285768c
Create Date: 2026-10-13 15:13:14.168026

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "da9e271523e2"
down_revision: Union[str, None] = "2f741285768c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adoption replays migrations on existing schemas; preserve existing data.
    h.add_column_if_missing(
        "integration_policy",
        sa.Column("allow_any_origin", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("integration_policy", "allow_any_origin")
