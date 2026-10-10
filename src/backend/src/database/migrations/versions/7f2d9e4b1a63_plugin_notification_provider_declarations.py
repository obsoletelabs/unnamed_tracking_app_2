"""Persist generic plugin notification destination declarations.

Revision ID: 7f2d9e4b1a63
Revises: 08cca40cdb7f
"""

from alembic import op
import sqlalchemy as sa

revision: str = "7f2d9e4b1a63"
down_revision: str | None = "08cca40cdb7f"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "plugin_notification_provider_registrations",
        sa.Column("destination_kind", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "plugin_notification_provider_registrations",
        sa.Column("channel_context", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "plugin_notification_provider_registrations",
        sa.Column("privacy", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE plugin_notification_provider_registrations "
            "SET destination_kind = 'legacy_webhook', channel_context = 'external', privacy = 0 "
            "WHERE destination_kind IS NULL"
        )
    )
    op.alter_column("plugin_notification_provider_registrations", "destination_kind", nullable=False)
    op.alter_column("plugin_notification_provider_registrations", "channel_context", nullable=False)
    op.alter_column("plugin_notification_provider_registrations", "privacy", nullable=False)


def downgrade() -> None:
    op.drop_column("plugin_notification_provider_registrations", "privacy")
    op.drop_column("plugin_notification_provider_registrations", "channel_context")
    op.drop_column("plugin_notification_provider_registrations", "destination_kind")
