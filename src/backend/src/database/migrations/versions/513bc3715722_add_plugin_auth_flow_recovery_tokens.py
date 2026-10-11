"""add plugin auth flow recovery tokens

Revision ID: 513bc3715722
Revises: d3b2af990c5d
Create Date: 2026-10-11 03:29:57.202565

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "513bc3715722"
down_revision: Union[str, None] = "d3b2af990c5d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated with Compose/Alembic; use existing adoption-safe helpers.
    h.create_table_if_missing(
        "auth_request_limits",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    h.create_index_if_missing(
        op.f("ix_auth_request_limits_expires_at"),
        "auth_request_limits",
        ["expires_at"],
        unique=False,
    )
    h.create_table_if_missing(
        "plugin_auth_flows",
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("installation_id", sa.UUID(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("registered_at", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("mode"),
    )
    h.create_table_if_missing(
        "auth_recovery_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("installation_id", sa.UUID(), nullable=False),
        sa.Column("flow_revision", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.Column("account_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("notification_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("used_at", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    h.create_index_if_missing(
        op.f("ix_auth_recovery_tokens_expires_at"),
        "auth_recovery_tokens",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    # Generated with Compose/Alembic; use existing adoption-safe helpers.
    op.drop_index(op.f("ix_auth_recovery_tokens_expires_at"), table_name="auth_recovery_tokens")
    op.drop_table("auth_recovery_tokens")
    op.drop_table("plugin_auth_flows")
    op.drop_index(op.f("ix_auth_request_limits_expires_at"), table_name="auth_request_limits")
    op.drop_table("auth_request_limits")
