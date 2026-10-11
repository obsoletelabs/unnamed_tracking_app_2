"""Persist encrypted Steam owner sessions.

Revision ID: def2e465ec9f
Revises: 513bc3715722
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "def2e465ec9f"
down_revision: str | None = "513bc3715722"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Reviewed autogeneration: unrelated note-table drops were discarded.
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    for name, column_type in (
        ("steam_access_token", sa.Text()),
        ("steam_refresh_token", sa.Text()),
        ("steam_token_expires_at", sa.BigInteger()),
        ("steam_login_state", sa.Text()),
        ("steam_library_owner_id", sa.String(17)),
    ):
        if name not in existing:
            op.add_column("users", sa.Column(name, column_type, nullable=True))
    op.execute(
        sa.text("""
        UPDATE users SET steam_library_owner_id = steam_id
        WHERE steam_library_owner_id IS NULL AND steam_id ~ '^[0-9]{17}$'
          AND EXISTS (SELECT 1 FROM games WHERE games.user_id = users.id AND source = 'Steam')
    """)
    )


def downgrade() -> None:
    op.drop_column("users", "steam_library_owner_id")
    op.drop_column("users", "steam_login_state")
    op.drop_column("users", "steam_token_expires_at")
    op.drop_column("users", "steam_refresh_token")
    op.drop_column("users", "steam_access_token")
