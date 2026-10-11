"""add bounded invitation notification recipients

Revision ID: f1b4375a624c
Revises: def2e465ec9f
Create Date: 2026-10-11 04:37:06.663176

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "f1b4375a624c"
down_revision: Union[str, None] = "def2e465ec9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_constraint(table: str, name: str) -> bool:
    # A matching name in another schema is not evidence for this relation.
    return (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_constraint WHERE conname = :name AND conrelid = to_regclass(:table)"
            ),
            {"name": name, "table": table},
        )
        .first()
        is not None
    )


def upgrade() -> None:
    # Generated with Compose/Alembic, reviewed and made adoption-safe.
    # Excludes unrelated game-note drops from the separately reported env import issue.
    h.create_table_if_missing(
        "notification_pending_recipients",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("retired_at", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    h.create_index_if_missing(
        op.f("ix_notification_pending_recipients_expires_at"),
        "notification_pending_recipients",
        ["expires_at"],
        unique=False,
    )
    h.create_table_if_missing(
        "user_invitations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pending_recipient_id", sa.UUID(), nullable=False),
        sa.Column("issued_by", sa.UUID(), nullable=False),
        sa.Column("plugin_id", sa.String(length=128), nullable=False),
        sa.Column("installation_id", sa.UUID(), nullable=False),
        sa.Column("flow_revision", sa.Integer(), nullable=False),
        sa.Column("email_digest", sa.String(length=64), nullable=False),
        sa.Column("encrypted_email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.Column("app_url", sa.String(length=2048), nullable=False),
        sa.Column("notification_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
        sa.Column("used_at", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["issued_by"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["pending_recipient_id"], ["notification_pending_recipients.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pending_recipient_id"),
    )
    h.create_index_if_missing(
        op.f("ix_user_invitations_email_digest"), "user_invitations", ["email_digest"], unique=False
    )
    h.create_index_if_missing(
        op.f("ix_user_invitations_expires_at"), "user_invitations", ["expires_at"], unique=False
    )
    h.add_column_if_missing(
        "notification_destinations", sa.Column("pending_recipient_id", sa.UUID(), nullable=True)
    )
    op.alter_column("notification_destinations", "user_id", existing_type=sa.UUID(), nullable=True)
    if not _has_constraint("notification_destinations", "uq_notification_pending_endpoint"):
        op.create_unique_constraint(
            "uq_notification_pending_endpoint",
            "notification_destinations",
            ["pending_recipient_id", "provider_id", "endpoint_key"],
        )
    if not _has_constraint(
        "notification_destinations", "fk_notification_destinations_pending_recipient"
    ):
        op.create_foreign_key(
            "fk_notification_destinations_pending_recipient",
            "notification_destinations",
            "notification_pending_recipients",
            ["pending_recipient_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if not _has_constraint(
        "notification_destinations", "ck_notification_destination_recipient_owner"
    ):
        op.create_check_constraint(
            "ck_notification_destination_recipient_owner",
            "notification_destinations",
            "(user_id IS NULL) <> (pending_recipient_id IS NULL)",
        )
    h.add_column_if_missing(
        "notifications", sa.Column("pending_recipient_id", sa.UUID(), nullable=True)
    )
    op.alter_column("notifications", "user_id", existing_type=sa.UUID(), nullable=True)
    if not _has_constraint("notifications", "uq_notifications_pending_dedupe"):
        op.create_unique_constraint(
            "uq_notifications_pending_dedupe",
            "notifications",
            ["pending_recipient_id", "dedupe_key"],
        )
    if not _has_constraint("notifications", "fk_notifications_pending_recipient"):
        op.create_foreign_key(
            "fk_notifications_pending_recipient",
            "notifications",
            "notification_pending_recipients",
            ["pending_recipient_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if not _has_constraint("notifications", "ck_notifications_recipient_owner"):
        op.create_check_constraint(
            "ck_notifications_recipient_owner",
            "notifications",
            "(user_id IS NULL) <> (pending_recipient_id IS NULL)",
        )
    # ### end Alembic commands ###


def downgrade() -> None:
    # Delete only bootstrap state that cannot be represented in the prior schema.
    op.execute(
        "DELETE FROM notification_deliveries WHERE notification_id IN "
        "(SELECT id FROM notifications WHERE pending_recipient_id IS NOT NULL)"
    )
    op.execute("DELETE FROM notification_pending_recipients")
    op.drop_constraint("ck_notifications_recipient_owner", "notifications", type_="check")
    op.drop_constraint("fk_notifications_pending_recipient", "notifications", type_="foreignkey")
    op.drop_constraint("uq_notifications_pending_dedupe", "notifications", type_="unique")
    op.alter_column("notifications", "user_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("notifications", "pending_recipient_id")
    op.drop_constraint(
        "ck_notification_destination_recipient_owner", "notification_destinations", type_="check"
    )
    op.drop_constraint(
        "fk_notification_destinations_pending_recipient",
        "notification_destinations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_notification_pending_endpoint", "notification_destinations", type_="unique"
    )
    op.alter_column("notification_destinations", "user_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("notification_destinations", "pending_recipient_id")
    op.drop_index(op.f("ix_user_invitations_expires_at"), table_name="user_invitations")
    op.drop_index(op.f("ix_user_invitations_email_digest"), table_name="user_invitations")
    op.drop_table("user_invitations")
    op.drop_index(
        op.f("ix_notification_pending_recipients_expires_at"),
        table_name="notification_pending_recipients",
    )
    op.drop_table("notification_pending_recipients")
    # ### end Alembic commands ###
