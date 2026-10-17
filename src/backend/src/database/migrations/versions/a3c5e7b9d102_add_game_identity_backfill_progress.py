"""Add the game identity backfill progress table.

Revision ID: a3c5e7b9d102
Revises: 716a1e2c451d
Create Date: 2026-10-17 09:00:00.000000

Phase 6 of the game identity overhaul (#102). The migration is additive: one
table that records, per user, how far the resumable library backfill got. The
backfill itself is deliberately not an Alembic data step: it runs per user in
bounded transactions, can be interrupted and resumed, reports counts, and must
not hold the schema-migration lock (or fail container start-up) on a large
library. See wiki/docs/development/game-identity-migration.md.

Review notes: autogenerate proposed only this table; nothing existing is
altered or dropped.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

revision: str = "a3c5e7b9d102"
down_revision: str | None = "716a1e2c451d"
branch_labels: str | None = None
depends_on: str | None = None


def _json(name: str, default: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text(default),
        nullable=False,
    )


def upgrade() -> None:
    h.create_table_if_missing(
        "game_identity_backfills",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=True),
        _json("completed_stages", "'[]'::jsonb"),
        _json("counts", "'{}'::jsonb"),
        _json("before_counts", "'{}'::jsonb"),
        _json("after_counts", "'{}'::jsonb"),
        _json("errors", "'[]'::jsonb"),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=True),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'incomplete', 'failed')",
            name="ck_game_identity_backfills_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    # Progress and error reports only; every identity row the backfill wrote is kept
    # (and the previous migration refuses to downgrade while those rows exist).
    if h.has_table("game_identity_backfills"):
        op.drop_table("game_identity_backfills")
