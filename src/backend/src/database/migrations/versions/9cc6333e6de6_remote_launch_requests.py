"""Persist grant-bound Playnite receivers and one-use launch requests.

Revision ID: 9cc6333e6de6
Revises: c4e9a2d71b05
Create Date: 2026-10-30 07:54:31.369542

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "9cc6333e6de6"
down_revision: Union[str, None] = "c4e9a2d71b05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    h.create_table_if_missing(
        "remote_launch_devices",
        sa.Column("grant_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["grant_id"], ["delegated_grants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("grant_id"),
    )
    h.create_table_if_missing(
        "remote_launch_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=False),
        sa.Column("playnite_id", sa.UUID(), nullable=False),
        sa.Column("request_key", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("dispatched_at", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'dispatched', 'accepted', 'failed', 'expired', 'unknown')",
            name="ck_remote_launch_requests_state",
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["remote_launch_devices.grant_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "request_key", name="uq_remote_launch_requests_user_key"),
    )
    h.create_index_if_missing(
        "ix_remote_launch_requests_game_id", "remote_launch_requests", ["game_id"], unique=False
    )
    h.create_index_if_missing(
        "ix_remote_launch_requests_device_state",
        "remote_launch_requests",
        ["device_id", "state"],
        unique=False,
    )
    h.create_index_if_missing(
        "ix_remote_launch_requests_expires", "remote_launch_requests", ["expires_at"], unique=False
    )
    h.create_index_if_missing(
        "ix_remote_launch_requests_user_created",
        "remote_launch_requests",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_remote_launch_requests_user_created", table_name="remote_launch_requests")
    op.drop_index("ix_remote_launch_requests_expires", table_name="remote_launch_requests")
    op.drop_index("ix_remote_launch_requests_device_state", table_name="remote_launch_requests")
    op.drop_index("ix_remote_launch_requests_game_id", table_name="remote_launch_requests")
    op.drop_table("remote_launch_requests")
    op.drop_table("remote_launch_devices")
