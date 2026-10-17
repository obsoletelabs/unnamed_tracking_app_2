"""Persist notification stories, revisions and item links.

Revision ID: 6f75008c02a6
Revises: c10098e58426
Create Date: 2026-10-16 22:15:32.215123
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6f75008c02a6"
down_revision: Union[str, None] = "c10098e58426"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive only: existing notifications keep their IDs, read state and receipts and remain
    # implicit single-item stories (story_id IS NULL). No backfill is required or performed.
    op.create_table(
        "notification_stories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("story_key", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "event_type", "scope_key", "story_key", name="uq_notification_story_identity"
        ),
    )
    op.create_table(
        "notification_story_revisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("story_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("notification_id", sa.UUID(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("receipt_key", sa.String(length=200), nullable=False),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.Column("accepted_at", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["story_id"], ["notification_stories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("story_id", "revision", name="uq_notification_story_revision"),
    )
    op.add_column(
        "notification_receipts", sa.Column("story_key", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "notification_receipts", sa.Column("story_phase", sa.String(length=64), nullable=True)
    )
    op.add_column("notifications", sa.Column("story_id", sa.UUID(), nullable=True))
    op.add_column("notifications", sa.Column("story_revision", sa.Integer(), nullable=True))
    op.add_column("notifications", sa.Column("previous_notification_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_notifications_story",
        "notifications",
        "notification_stories",
        ["story_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_notifications_previous",
        "notifications",
        "notifications",
        ["previous_notification_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_notifications_story", "notifications", ["story_id"])
    op.create_unique_constraint(
        "uq_notifications_story_revision", "notifications", ["story_id", "story_revision"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM notification_stories)")):
        raise RuntimeError(
            "Refusing to drop populated notification story history; retain the schema or "
            "remove the stories explicitly before downgrading."
        )
    op.drop_constraint("uq_notifications_story_revision", "notifications", type_="unique")
    op.drop_index("ix_notifications_story", table_name="notifications")
    op.drop_constraint("fk_notifications_previous", "notifications", type_="foreignkey")
    op.drop_constraint("fk_notifications_story", "notifications", type_="foreignkey")
    op.drop_column("notifications", "previous_notification_id")
    op.drop_column("notifications", "story_revision")
    op.drop_column("notifications", "story_id")
    op.drop_column("notification_receipts", "story_phase")
    op.drop_column("notification_receipts", "story_key")
    op.drop_table("notification_story_revisions")
    op.drop_table("notification_stories")
