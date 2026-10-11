"""Persist encrypted Steam owner sessions.

Revision ID: def2e465ec9f
Revises: d3b2af990c5d
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "def2e465ec9f"
down_revision: str | None = "d3b2af990c5d"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Reviewed autogeneration: unrelated note-table drops were discarded.
    op.add_column("users", sa.Column("steam_access_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("steam_refresh_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("steam_token_expires_at", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("steam_login_state", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("steam_library_owner_id", sa.String(17), nullable=True))
    op.execute(
        sa.text("""
        UPDATE users SET steam_library_owner_id = steam_id
        WHERE steam_id ~ '^[0-9]{17}$'
          AND EXISTS (SELECT 1 FROM games WHERE games.user_id = users.id AND source = 'Steam')
    """)
    )


def downgrade() -> None:
    op.drop_column("users", "steam_library_owner_id")
    op.drop_column("users", "steam_login_state")
    op.drop_column("users", "steam_token_expires_at")
    op.drop_column("users", "steam_refresh_token")
    op.drop_column("users", "steam_access_token")
