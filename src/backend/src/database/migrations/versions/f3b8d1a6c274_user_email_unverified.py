"""mark emails the user typed themselves as unverified

Revision ID: f3b8d1a6c274
Revises: b7d2e4f19a38
Create Date: 2026-10-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "f3b8d1a6c274"
down_revision: Union[str, Sequence[str], None] = "b7d2e4f19a38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing accounts keep their current behaviour: how their address was set is not recorded.
    h.add_column_if_missing(
        "users",
        sa.Column(
            "email_unverified", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "email_unverified")
