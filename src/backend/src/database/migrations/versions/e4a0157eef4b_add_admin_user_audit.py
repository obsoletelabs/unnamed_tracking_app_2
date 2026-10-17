"""add admin user audit

Revision ID: e4a0157eef4b
Revises: b87b7429ece2
Create Date: 2026-10-16 22:14:45.783663

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4a0157eef4b"
down_revision: Union[str, None] = "b87b7429ece2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("admin_user_audit"):
        return

    op.create_table(
        "admin_user_audit",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("actor_username", sa.String(length=100), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column("target_label", sa.String(length=320), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.String(length=512), nullable=False),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_admin_user_audit_actor_id"), "admin_user_audit", ["actor_id"], unique=False
    )
    op.create_index(
        op.f("ix_admin_user_audit_target_id"), "admin_user_audit", ["target_id"], unique=False
    )


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("admin_user_audit"):
        return

    op.drop_index(op.f("ix_admin_user_audit_target_id"), table_name="admin_user_audit")
    op.drop_index(op.f("ix_admin_user_audit_actor_id"), table_name="admin_user_audit")
    op.drop_table("admin_user_audit")
