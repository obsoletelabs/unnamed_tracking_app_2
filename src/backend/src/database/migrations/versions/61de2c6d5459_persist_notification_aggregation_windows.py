"""Persist notification aggregation windows and delivery batch membership.

Revision ID: 61de2c6d5459
Revises: 6f75008c02a6
Create Date: 2026-10-17 03:15:13.647277
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

revision: str = "61de2c6d5459"
down_revision: Union[str, None] = "6f75008c02a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive only: queued and sent deliveries keep their state; they simply belong to no window.
    h.create_table_if_missing(
        "notification_aggregation_windows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("destination_id", sa.UUID(), nullable=False),
        sa.Column("destination_revision", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("strategy", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("window_end", sa.BigInteger(), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("sealed_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["destination_id"], ["notification_destinations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    h.create_index_if_missing(
        "ix_notification_window_due", "notification_aggregation_windows", ["status", "window_end"]
    )
    h.create_index_if_missing(
        "uq_notification_window_open",
        "notification_aggregation_windows",
        ["destination_id", "destination_revision", "strategy", "scope_key"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    h.add_column_if_missing(
        "notification_deliveries", sa.Column("window_id", sa.UUID(), nullable=True)
    )
    h.add_column_if_missing(
        "notification_deliveries", sa.Column("batch_id", sa.UUID(), nullable=True)
    )
    h.add_column_if_missing(
        "notification_deliveries", sa.Column("batch_index", sa.Integer(), nullable=True)
    )
    h.create_index_if_missing(
        "ix_notification_delivery_batch", "notification_deliveries", ["batch_id"]
    )
    h.create_index_if_missing(
        "ix_notification_delivery_window", "notification_deliveries", ["window_id"]
    )
    if not h.has_constraint("notification_deliveries", "fk_notification_delivery_window"):
        op.create_foreign_key(
            "fk_notification_delivery_window",
            "notification_deliveries",
            "notification_aggregation_windows",
            ["window_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    # Held work must not be stranded by removing its windows: release it as ordinary work.
    if h.has_table("notification_deliveries"):
        op.execute(
            sa.text(
                "UPDATE notification_deliveries SET status = 'pending', claim_token = NULL, "
                "lease_until = NULL WHERE status = 'aggregating'"
            )
        )
    if h.has_constraint("notification_deliveries", "fk_notification_delivery_window"):
        op.drop_constraint(
            "fk_notification_delivery_window", "notification_deliveries", type_="foreignkey"
        )
    h.drop_index_if_exists("ix_notification_delivery_window", "notification_deliveries")
    h.drop_index_if_exists("ix_notification_delivery_batch", "notification_deliveries")
    for column in ("batch_index", "batch_id", "window_id"):
        if h.has_column("notification_deliveries", column):
            op.drop_column("notification_deliveries", column)
    h.drop_index_if_exists("uq_notification_window_open", "notification_aggregation_windows")
    h.drop_index_if_exists("ix_notification_window_due", "notification_aggregation_windows")
    if h.has_table("notification_aggregation_windows"):
        op.drop_table("notification_aggregation_windows")
