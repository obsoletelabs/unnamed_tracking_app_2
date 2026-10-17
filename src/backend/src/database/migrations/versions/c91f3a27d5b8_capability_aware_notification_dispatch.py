"""Per-revision deliveries, attempt operations and remote message references.

Revision ID: c91f3a27d5b8
Revises: b7d41c9a2e65
Create Date: 2026-10-17 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

revision: str = "c91f3a27d5b8"
down_revision: Union[str, None] = "b7d41c9a2e65"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNIQUE = "uq_notification_delivery_notification_destination"


def upgrade() -> None:
    # Additive. Existing deliveries present revision 0 ("before revisions were tracked").
    h.add_column_if_missing(
        "notification_deliveries",
        sa.Column("story_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    h.add_column_if_missing(
        "notification_deliveries",
        sa.Column("operation", sa.String(length=16), nullable=False, server_default="send"),
    )
    h.add_column_if_missing(
        "notification_deliveries", sa.Column("fallback", sa.String(length=160), nullable=True)
    )
    h.add_column_if_missing(
        "notification_delivery_attempts",
        sa.Column("operation", sa.String(length=16), nullable=False, server_default="send"),
    )
    h.add_column_if_missing(
        "notification_delivery_attempts", sa.Column("batch_id", sa.UUID(), nullable=True)
    )
    if h.has_constraint("notification_deliveries", UNIQUE):
        op.drop_constraint(UNIQUE, "notification_deliveries", type_="unique")
    op.create_unique_constraint(
        UNIQUE, "notification_deliveries", ["notification_id", "destination_id", "story_revision"]
    )
    h.create_table_if_missing(
        "notification_remote_references",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("notification_id", sa.UUID(), nullable=False),
        sa.Column("story_id", sa.UUID(), nullable=True),
        sa.Column("destination_id", sa.UUID(), nullable=False),
        sa.Column("destination_revision", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("remote_ref", sa.String(length=512), nullable=True),
        sa.Column("delivered_revision", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("edit_failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["story_id"], ["notification_stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["destination_id"], ["notification_destinations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "notification_id",
            "destination_id",
            "destination_revision",
            name="uq_notification_remote_reference",
        ),
    )
    h.create_index_if_missing(
        "ix_notification_remote_reference_story", "notification_remote_references", ["story_id"]
    )


def downgrade() -> None:
    # Per-revision deliveries and remote message references cannot be represented by the older
    # one-delivery-per-destination schema; erasing them would also erase delivery history.
    connection = op.get_bind()
    if h.has_table("notification_remote_references") and connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM notification_remote_references)")
    ):
        raise RuntimeError(
            "Refusing to drop stored remote message references; retain the schema or "
            "remove them explicitly before downgrading."
        )
    if h.has_column("notification_deliveries", "story_revision") and connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM notification_deliveries WHERE destination_id IS NOT NULL "
            "GROUP BY notification_id, destination_id HAVING count(*) > 1)"
        )
    ):
        raise RuntimeError(
            "Refusing to merge per-revision deliveries; retain the schema or remove the "
            "superseded revisions' deliveries explicitly before downgrading."
        )
    h.drop_index_if_exists(
        "ix_notification_remote_reference_story", "notification_remote_references"
    )
    if h.has_table("notification_remote_references"):
        op.drop_table("notification_remote_references")
    if h.has_constraint("notification_deliveries", UNIQUE):
        op.drop_constraint(UNIQUE, "notification_deliveries", type_="unique")
    op.create_unique_constraint(
        UNIQUE, "notification_deliveries", ["notification_id", "destination_id"]
    )
    for table, column in (
        ("notification_delivery_attempts", "batch_id"),
        ("notification_delivery_attempts", "operation"),
        ("notification_deliveries", "fallback"),
        ("notification_deliveries", "operation"),
        ("notification_deliveries", "story_revision"),
    ):
        if h.has_column(table, column):
            op.drop_column(table, column)
