"""add per-game preferred metadata sources

Revision ID: b7d2e4f19a38
Revises: e5a1c7d93b46
Create Date: 2026-10-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "b7d2e4f19a38"
down_revision: Union[str, Sequence[str], None] = "e5a1c7d93b46"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "game_field_preferences"


def upgrade() -> None:
    h.create_table_if_missing(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("game_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False),
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("btrim(field) <> ''", name="ck_game_field_preferences_field"),
        sa.CheckConstraint("btrim(source_key) <> ''", name="ck_game_field_preferences_source_key"),
        sa.ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            name="fk_game_field_preferences_game",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "field", name="uq_game_field_preferences_game_field"),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
