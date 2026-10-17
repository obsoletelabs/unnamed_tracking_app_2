"""add optional user display name

Revision ID: a7d3c91e5b20
Revises: e4a0157eef4b
Create Date: 2026-10-16 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d3c91e5b20"
down_revision: Union[str, None] = "e4a0157eef4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable and without a default: existing accounts keep showing their username.
    # Databases adopted from an older history replay later migrations, so skip when present.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "display_name" not in columns:
        op.add_column("users", sa.Column("display_name", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")
