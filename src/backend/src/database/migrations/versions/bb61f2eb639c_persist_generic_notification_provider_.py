"""Persist generic notification provider definitions without changing existing destinations.

Revision ID: bb61f2eb639c
Revises: 0037e34ac954
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bb61f2eb639c"
down_revision: str | None = "0037e34ac954"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "plugin_notification_provider_registrations"
        )
    }
    if "definition" in columns:
        return
    op.add_column(
        "plugin_notification_provider_registrations",
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM plugin_notification_provider_registrations WHERE transport = 'plugin'"
        )
    ):
        raise RuntimeError("Cannot discard generic notification provider definitions")
    op.drop_column("plugin_notification_provider_registrations", "definition")
