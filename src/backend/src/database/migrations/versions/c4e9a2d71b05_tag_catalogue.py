"""add the tag catalogue: private and default labels, aliases and suppressions

Revision ID: c4e9a2d71b05
Revises: f3b8d1a6c274
Create Date: 2026-10-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "c4e9a2d71b05"
down_revision: Union[str, Sequence[str], None] = "f3b8d1a6c274"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create empty tables; no existing tag moves (the raw arrays stay the source of truth)."""
    uuid = postgresql.UUID(as_uuid=True)
    h.create_table_if_missing(
        "user_tags",
        sa.Column("id", uuid, nullable=False),
        sa.Column("user_id", uuid, nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("namespace", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("archived_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("btrim(label) <> ''", name="ck_user_tags_label"),
        sa.CheckConstraint("btrim(key) <> ''", name="ck_user_tags_key"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", name="uq_user_tags_user_key"),
        sa.UniqueConstraint("user_id", "id", name="uq_user_tags_user_id_id"),
    )
    h.create_table_if_missing(
        "user_tag_aliases",
        sa.Column("id", uuid, nullable=False),
        sa.Column("user_id", uuid, nullable=False),
        sa.Column("tag_id", uuid, nullable=False),
        sa.Column("alias_key", sa.String(length=160), nullable=False),
        sa.Column("alias_label", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("btrim(alias_key) <> ''", name="ck_user_tag_aliases_key"),
        sa.ForeignKeyConstraint(
            ["user_id", "tag_id"],
            ["user_tags.user_id", "user_tags.id"],
            name="fk_user_tag_aliases_tag",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "alias_key", name="uq_user_tag_aliases_user_alias"),
    )
    h.create_table_if_missing(
        "user_tag_suppressions",
        sa.Column("id", uuid, nullable=False),
        sa.Column("user_id", uuid, nullable=False),
        sa.Column("tag_key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "tag_key", name="uq_user_tag_suppressions_user_key"),
    )
    h.create_table_if_missing(
        "default_tags",
        sa.Column("id", uuid, nullable=False),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("archived_at", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("btrim(label) <> ''", name="ck_default_tags_label"),
        sa.CheckConstraint("btrim(key) <> ''", name="ck_default_tags_key"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_default_tags_key"),
    )
    h.create_table_if_missing(
        "default_tag_aliases",
        sa.Column("id", uuid, nullable=False),
        sa.Column("tag_id", uuid, nullable=False),
        sa.Column("alias_key", sa.String(length=160), nullable=False),
        sa.Column("alias_label", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("btrim(alias_key) <> ''", name="ck_default_tag_aliases_key"),
        sa.ForeignKeyConstraint(["tag_id"], ["default_tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias_key", name="uq_default_tag_aliases_alias"),
    )


def downgrade() -> None:
    """Drop the catalogue tables. Raw tags are untouched; export the catalogue first to keep it."""
    for table in (
        "default_tag_aliases",
        "default_tags",
        "user_tag_suppressions",
        "user_tag_aliases",
        "user_tags",
    ):
        op.drop_table(table)
