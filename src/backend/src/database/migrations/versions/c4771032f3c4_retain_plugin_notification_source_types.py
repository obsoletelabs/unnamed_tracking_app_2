"""Retain plugin notification source types.

Revision ID: c4771032f3c4
Revises: 24bc18feb852
Create Date: 2026-10-09 11:40:41.323045
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4771032f3c4"
down_revision: Union[str, None] = "24bc18feb852"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adopt databases initialized through metadata as well as older migrations.
    inspector = sa.inspect(op.get_bind())
    table = "plugin_notification_type_registrations"
    if not inspector.has_table(table):
        op.create_table(
            table,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("plugin_id", sa.String(128), nullable=False),
            sa.Column("installation_id", sa.UUID(), nullable=False),
            sa.Column("event_type", sa.String(128), nullable=False),
            sa.Column("definition", postgresql.JSONB(), nullable=False),
            sa.Column("registered_at", sa.BigInteger(), nullable=False),
            sa.Column("revoked_at", sa.BigInteger(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_type"),
        )
        for column in ("installation_id", "plugin_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    if "accepted_at" not in {
        column["name"] for column in inspector.get_columns("notification_receipts")
    }:
        op.add_column(
            "notification_receipts",
            sa.Column("accepted_at", sa.BigInteger(), server_default="0", nullable=False),
        )
    if "source_installation_id" not in {
        column["name"] for column in inspector.get_columns("notifications")
    }:
        op.add_column(
            "notifications", sa.Column("source_installation_id", sa.UUID(), nullable=True)
        )


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM plugin_notification_type_registrations)")
        ).scalar()
        or connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM notifications WHERE source_installation_id IS NOT NULL)"
            )
        ).scalar()
    ):
        raise RuntimeError(
            "Cannot remove retained notification source identities; export them first"
        )
    op.drop_column("notifications", "source_installation_id")
    op.drop_column("notification_receipts", "accepted_at")
    op.drop_table("plugin_notification_type_registrations")
