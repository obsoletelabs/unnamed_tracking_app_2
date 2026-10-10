"""Remember game duplicate keep-both decisions.

Revision ID: 08cca40cdb7f
Revises: 42bb6ebaa05e
Create Date: 2026-10-10 06:36:41.506679

Generated through Compose/Alembic and reviewed. Unrelated historical note
tables detected by autogenerate are deliberately preserved.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "08cca40cdb7f"
down_revision: str | None = "42bb6ebaa05e"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "game_duplicate_dismissals",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("first_game_id", sa.UUID(), nullable=False),
        sa.Column("second_game_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("first_game_id < second_game_id", name="ordered_pair"),
        sa.ForeignKeyConstraint(["first_game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["second_game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "first_game_id", "second_game_id"),
    )


def downgrade() -> None:
    op.drop_table("game_duplicate_dismissals")
