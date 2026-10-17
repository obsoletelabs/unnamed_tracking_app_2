"""add Steam OpenID verified identity and redirect state

Revision ID: a80c5e1d7b42
Revises: 50b007897f73
Create Date: 2026-10-16 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a80c5e1d7b42"
down_revision: Union[str, None] = "50b007897f73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Startup adoption may encounter a complete current schema, so skip existing columns.
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "steam_verified_id" not in existing:
        op.add_column("users", sa.Column("steam_verified_id", sa.String(length=17), nullable=True))
    if "steam_openid_state" not in existing:
        op.add_column("users", sa.Column("steam_openid_state", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "steam_openid_state")
    op.drop_column("users", "steam_verified_id")
