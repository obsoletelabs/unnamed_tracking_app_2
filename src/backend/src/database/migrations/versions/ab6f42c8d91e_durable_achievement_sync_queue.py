"""durable achievement sync queue

Revision ID: ab6f42c8d91e
Revises: a97470898855, 65acf36995e5, f1b4375a624c, f186cf8aa5c4, d2b7e4a91c36, b7e3d92a41c8, a1c2e4f7b920
Create Date: 2026-10-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "ab6f42c8d91e"
down_revision: Union[str, Sequence[str], None] = (
    "a97470898855",
    "65acf36995e5",
    "f1b4375a624c",
    "f186cf8aa5c4",
    "d2b7e4a91c36",
    "b7e3d92a41c8",
    "a1c2e4f7b920",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    h.create_table_if_missing(
        "achievement_sync_queue",
        sa.Column("game_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("queued_at", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_id"),
    )
    h.create_index_if_missing(
        "ix_achievement_sync_queue_user_id", "achievement_sync_queue", ["user_id"], unique=False
    )
    h.create_index_if_missing(
        "ix_achievement_sync_queue_user_queued",
        "achievement_sync_queue",
        ["user_id", "queued_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_achievement_sync_queue_user_queued", table_name="achievement_sync_queue")
    op.drop_index("ix_achievement_sync_queue_user_id", table_name="achievement_sync_queue")
    op.drop_table("achievement_sync_queue")
