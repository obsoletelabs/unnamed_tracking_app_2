"""public app external principals and delegated connections

Revision ID: f28e1f724191
Revises: 150faa21e2c0
Create Date: 2026-10-13 13:12:56.790318

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "f28e1f724191"
down_revision: Union[str, None] = "150faa21e2c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adoption replays migrations on existing schemas; preserve existing data.
    h.create_table_if_missing(
        "public_app_actors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("installation_id", sa.UUID(), nullable=False),
        sa.Column("provider_installation_id", sa.UUID(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("installation_id", "subject"),
    )
    h.create_index_if_missing(
        op.f("ix_public_app_actors_expires_at"), "public_app_actors", ["expires_at"], unique=False
    )
    h.create_table_if_missing(
        "public_app_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("room", sa.String(length=255), nullable=False),
        sa.Column("verified_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["public_app_actors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    h.create_index_if_missing(
        op.f("ix_public_app_sessions_actor_id"), "public_app_sessions", ["actor_id"], unique=False
    )
    h.create_index_if_missing(
        op.f("ix_public_app_sessions_expires_at"),
        "public_app_sessions",
        ["expires_at"],
        unique=False,
    )
    h.create_index_if_missing(
        op.f("ix_public_app_sessions_room"), "public_app_sessions", ["room"], unique=False
    )
    h.create_table_if_missing(
        "public_app_sources",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("origin", sa.String(length=512), nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("verifier", sa.Text(), nullable=True),
        sa.Column("credential", sa.Text(), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("last_checked_at", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("library", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["public_app_actors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("actor_id", "origin"),
    )
    h.create_index_if_missing(
        op.f("ix_public_app_sources_actor_id"), "public_app_sources", ["actor_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_public_app_sources_actor_id"), table_name="public_app_sources")
    op.drop_table("public_app_sources")
    op.drop_index(op.f("ix_public_app_sessions_room"), table_name="public_app_sessions")
    op.drop_index(op.f("ix_public_app_sessions_expires_at"), table_name="public_app_sessions")
    op.drop_index(op.f("ix_public_app_sessions_actor_id"), table_name="public_app_sessions")
    op.drop_table("public_app_sessions")
    op.drop_index(op.f("ix_public_app_actors_expires_at"), table_name="public_app_actors")
    op.drop_table("public_app_actors")
