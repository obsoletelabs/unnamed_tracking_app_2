"""Track plugin notification server configuration authority.

Revision ID: d3b2af990c5d
Revises: bb61f2eb639c
Generated through Compose/Alembic; unrelated historical tables preserved.
"""

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

revision: str = "d3b2af990c5d"
down_revision: str | None = "bb61f2eb639c"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    h.add_column_if_missing(
        "plugin_notification_provider_registrations",
        sa.Column("server_revision", sa.Integer(), server_default="1", nullable=False),
    )
    h.add_column_if_missing(
        "plugin_notification_provider_registrations",
        sa.Column("server_configured", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM plugin_notification_provider_registrations WHERE transport = 'plugin'"
        )
    ):
        raise RuntimeError("Cannot discard plugin notification configuration authority")
    op.drop_column("plugin_notification_provider_registrations", "server_configured")
    op.drop_column("plugin_notification_provider_registrations", "server_revision")
