"""persist scoped notification lifecycle replay

Revision ID: 0037e34ac954
Revises: 08cca40cdb7f
Create Date: 2026-10-10 06:31:50.654690

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0037e34ac954"
down_revision: Union[str, None] = "08cca40cdb7f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    expected = {
        "notification_lifecycle_stream": {"id", "published_through", "expired_through"},
        "notification_lifecycle_outbox": {
            "id",
            "position",
            "user_id",
            "plugin_id",
            "installation_id",
            "notification_id",
            "delivery_id",
            "status",
            "occurred_at",
        },
        "notification_lifecycle_audit": {
            "sequence",
            "id",
            "published_at",
            "user_id",
            "plugin_id",
            "installation_id",
            "notification_id",
            "delivery_id",
            "status",
            "occurred_at",
        },
    }
    present = set(inspector.get_table_names()) & set(expected)
    if present:
        if present != set(expected) or any(
            {column["name"] for column in inspector.get_columns(table)} != columns
            for table, columns in expected.items()
        ):
            raise RuntimeError("Lifecycle schema is incomplete; restore or repair before adoption")
        return
    # Generated with Compose/Alembic; unrelated historical note tables preserved.
    op.create_table(
        "notification_lifecycle_stream",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("published_through", sa.BigInteger(), nullable=False),
        sa.Column("expired_through", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "notification_lifecycle_audit",
        sa.Column("sequence", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("published_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("installation_id", sa.UUID(), nullable=False),
        sa.Column("notification_id", sa.UUID(), nullable=False),
        sa.Column("delivery_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("id"),
    )
    op.create_index(
        "ix_notification_audit_retention",
        "notification_lifecycle_audit",
        ["published_at", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_notification_audit_scope",
        "notification_lifecycle_audit",
        ["user_id", "installation_id", "sequence"],
        unique=False,
    )
    op.create_table(
        "notification_lifecycle_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("position", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("installation_id", sa.UUID(), nullable=False),
        sa.Column("notification_id", sa.UUID(), nullable=False),
        sa.Column("delivery_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_outbox_position",
        "notification_lifecycle_outbox",
        ["position"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    for table in ("notification_lifecycle_audit", "notification_lifecycle_outbox"):
        if op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
            raise RuntimeError("Cannot downgrade populated notification lifecycle history")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM notification_lifecycle_stream WHERE published_through > 0)"
        )
    ):
        raise RuntimeError("Cannot discard notification lifecycle cursor history")
    # Generated with Compose/Alembic; unrelated historical note tables preserved.
    op.drop_index("ix_notification_outbox_position", table_name="notification_lifecycle_outbox")
    op.drop_table("notification_lifecycle_outbox")
    op.drop_index("ix_notification_audit_scope", table_name="notification_lifecycle_audit")
    op.drop_index("ix_notification_audit_retention", table_name="notification_lifecycle_audit")
    op.drop_table("notification_lifecycle_audit")
    op.drop_table("notification_lifecycle_stream")
    # ### end Alembic commands ###
