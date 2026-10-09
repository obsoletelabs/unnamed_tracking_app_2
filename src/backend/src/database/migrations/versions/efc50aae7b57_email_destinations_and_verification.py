"""email destinations and verification

Revision ID: efc50aae7b57
Revises: 22ff87d4d01c
Create Date: 2026-10-08 14:23:54.023095

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "efc50aae7b57"
down_revision: Union[str, None] = "22ff87d4d01c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Startup adoption replays revisions against an already-current schema.
    # Recognize complete adoption without resetting proof, secrets or work.
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    additions = {
        "app_integration_settings": {
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_from_address",
            "smtp_tls_mode",
        },
        "notification_destinations": {"encrypted_configuration", "display_name"},
        "notification_deliveries": {"requested_urgency", "effective_urgency"},
        "notification_verifications": {
            "id",
            "destination_id",
            "user_id",
            "destination_revision",
            "notification_id",
            "code_digest",
            "encrypted_code",
            "created_at",
            "expires_at",
            "failures",
            "used_at",
        },
    }
    existing = {
        table: {column["name"] for column in inspector.get_columns(table)}
        if table in tables
        else set()
        for table in additions
    }
    if "notification_verifications" in tables or any(
        columns & existing[table] for table, columns in additions.items()
    ):
        if all(columns <= existing[table] for table, columns in additions.items()):
            return
        raise RuntimeError(
            "Email notification schema is incomplete; restore or repair before adoption"
        )
    op.create_table(
        "notification_verifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("destination_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("destination_revision", sa.Integer(), nullable=False),
        sa.Column("notification_id", sa.UUID(), nullable=True),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("encrypted_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("used_at", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["destination_id"], ["notification_destinations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_notification_verifications_destination_id"),
        "notification_verifications",
        ["destination_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_verifications_user_id"),
        "notification_verifications",
        ["user_id"],
        unique=False,
    )
    op.add_column(
        "app_integration_settings", sa.Column("smtp_host", sa.String(length=253), nullable=True)
    )
    op.add_column("app_integration_settings", sa.Column("smtp_port", sa.Integer(), nullable=True))
    op.add_column(
        "app_integration_settings", sa.Column("smtp_username", sa.String(length=254), nullable=True)
    )
    op.add_column("app_integration_settings", sa.Column("smtp_password", sa.Text(), nullable=True))
    op.add_column(
        "app_integration_settings",
        sa.Column("smtp_from_address", sa.String(length=254), nullable=True),
    )
    op.add_column(
        "app_integration_settings", sa.Column("smtp_tls_mode", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "notification_deliveries",
        sa.Column(
            "requested_urgency", sa.String(length=16), server_default="normal", nullable=False
        ),
    )
    op.add_column(
        "notification_deliveries",
        sa.Column(
            "effective_urgency", sa.String(length=16), server_default="normal", nullable=False
        ),
    )
    op.add_column(
        "notification_destinations", sa.Column("encrypted_configuration", sa.Text(), nullable=True)
    )
    op.add_column(
        "notification_destinations", sa.Column("display_name", sa.String(length=80), nullable=True)
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM notification_destinations WHERE provider_id = 'core.smtp' AND active) OR EXISTS (SELECT 1 FROM notification_deliveries WHERE provider_id = 'core.smtp' AND status IN ('pending', 'processing', 'retry_wait'))"
        )
    ):
        raise RuntimeError(
            "Remove active email destinations and finish or cancel SMTP deliveries before downgrading"
        )
    op.drop_column("notification_destinations", "display_name")
    op.drop_column("notification_destinations", "encrypted_configuration")
    op.drop_column("notification_deliveries", "effective_urgency")
    op.drop_column("notification_deliveries", "requested_urgency")
    op.drop_column("app_integration_settings", "smtp_tls_mode")
    op.drop_column("app_integration_settings", "smtp_from_address")
    op.drop_column("app_integration_settings", "smtp_password")
    op.drop_column("app_integration_settings", "smtp_username")
    op.drop_column("app_integration_settings", "smtp_port")
    op.drop_column("app_integration_settings", "smtp_host")
    op.drop_index(
        op.f("ix_notification_verifications_user_id"), table_name="notification_verifications"
    )
    op.drop_index(
        op.f("ix_notification_verifications_destination_id"),
        table_name="notification_verifications",
    )
    op.drop_table("notification_verifications")
