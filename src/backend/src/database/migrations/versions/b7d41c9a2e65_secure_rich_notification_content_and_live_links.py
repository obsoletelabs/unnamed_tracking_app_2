"""Persist canonical rich notification content and expiring live story links.

Revision ID: b7d41c9a2e65
Revises: a3c81e5d7f20
Create Date: 2026-10-17 09:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

revision: str = "b7d41c9a2e65"
down_revision: Union[str, None] = "a3c81e5d7f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive: existing revisions keep their title/body and have no canonical document.
    h.add_column_if_missing(
        "notification_story_revisions",
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    h.create_table_if_missing(
        "notification_live_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("story_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
        sa.Column("last_used_at", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["story_id"], ["notification_stories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    h.create_index_if_missing(
        "ix_notification_live_links_story", "notification_live_links", ["story_id"]
    )


def downgrade() -> None:
    h.refuse_if_populated(
        "notification_story_revisions", "rich notification story content", "content IS NOT NULL"
    )

    h.drop_index_if_exists("ix_notification_live_links_story", "notification_live_links")
    if h.has_table("notification_live_links"):
        op.drop_table("notification_live_links")
    if h.has_column("notification_story_revisions", "content"):
        op.drop_column("notification_story_revisions", "content")
