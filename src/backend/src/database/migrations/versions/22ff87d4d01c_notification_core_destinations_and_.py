"""notification core destinations and delivery lifecycle

Revision ID: 22ff87d4d01c
Revises: d8338e79fbbd
Create Date: 2026-10-08 11:34:40.121947

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "22ff87d4d01c"
down_revision: Union[str, None] = "d8338e79fbbd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Startup adoption stamps an already-current database at the baseline and
    # reruns later revisions. Never backfill or suppress its newer deliveries.
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    additions = {
        "notification_destinations",
        "notification_receipts",
        "notification_delivery_attempts",
    }
    if additions & tables:
        notice_columns = {column["name"] for column in inspector.get_columns("notifications")}
        delivery_columns = {
            column["name"] for column in inspector.get_columns("notification_deliveries")
        }
        if (
            additions <= tables
            and {"event_type", "deleted_at", "public_body"} <= notice_columns
            and {
                "destination_id",
                "destination_revision",
                "projection",
                "claim_token",
                "lease_until",
            }
            <= delivery_columns
        ):
            return
        raise RuntimeError("Notification schema is incomplete; restore or repair before adoption")
    op.create_table(
        "notification_destinations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("endpoint_key", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("channel_context", sa.String(length=16), nullable=False),
        sa.Column("privacy", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("verified_revision", sa.Integer(), nullable=True),
        sa.Column("verification_method", sa.String(length=64), nullable=True),
        sa.Column("verification_revoked_at", sa.BigInteger(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("installation_id", sa.UUID(), nullable=True),
        sa.Column("configuration_ref", sa.String(length=200), nullable=True),
        sa.Column("recovery_allowed", sa.Boolean(), nullable=False),
        sa.Column("media_consent_revision", sa.Integer(), nullable=True),
        sa.Column("media_consent_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "provider_id", "endpoint_key", name="uq_notification_endpoint"
        ),
    )
    op.create_table(
        "notification_receipts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.BigInteger(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notification_receipt_user_key"),
    )
    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("delivery_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=False),
        sa.Column("finished_at", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["delivery_id"], ["notification_deliveries.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", "number", name="uq_notification_attempt_number"),
    )
    op.add_column("notification_deliveries", sa.Column("destination_id", sa.UUID(), nullable=True))
    op.add_column(
        "notification_deliveries",
        sa.Column("destination_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "notification_deliveries",
        sa.Column("projection", sa.String(length=32), nullable=False, server_default="canonical"),
    )
    op.add_column("notification_deliveries", sa.Column("claim_token", sa.UUID(), nullable=True))
    op.add_column(
        "notification_deliveries", sa.Column("lease_until", sa.BigInteger(), nullable=True)
    )
    op.alter_column(
        "notification_deliveries",
        "status",
        existing_type=sa.VARCHAR(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.drop_constraint(
        "uq_notification_delivery_notification_provider", "notification_deliveries", type_="unique"
    )
    op.create_unique_constraint(
        "uq_notification_delivery_notification_destination",
        "notification_deliveries",
        ["notification_id", "destination_id"],
    )
    op.create_foreign_key(
        "fk_notification_delivery_destination",
        "notification_deliveries",
        "notification_destinations",
        ["destination_id"],
        ["id"],
    )
    op.add_column(
        "notifications",
        sa.Column("event_type", sa.String(length=128), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "notifications",
        sa.Column("source", sa.String(length=128), nullable=False, server_default="host"),
    )
    op.add_column(
        "notifications",
        sa.Column("required_trust", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "notifications",
        sa.Column("purpose", sa.String(length=32), nullable=False, server_default="standard"),
    )
    op.add_column(
        "notifications",
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="info"),
    )
    op.add_column("notifications", sa.Column("group_key", sa.String(length=200), nullable=True))
    op.add_column(
        "notifications",
        sa.Column("inbox_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("notifications", sa.Column("dismissed_at", sa.BigInteger(), nullable=True))
    op.add_column("notifications", sa.Column("deleted_at", sa.BigInteger(), nullable=True))
    op.add_column("notifications", sa.Column("expires_at", sa.BigInteger(), nullable=True))
    op.add_column("notifications", sa.Column("public_title", sa.String(length=500), nullable=True))
    op.add_column("notifications", sa.Column("public_body", sa.Text(), nullable=True))
    op.alter_column(
        "notifications",
        "kind",
        existing_type=sa.VARCHAR(length=30),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
    op.alter_column(
        "notifications",
        "media_type",
        existing_type=sa.VARCHAR(length=10),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    _backfill()
    for column in (
        "event_type",
        "source",
        "required_trust",
        "purpose",
        "severity",
        "inbox_visible",
    ):
        op.alter_column("notifications", column, server_default=None)
    for column in ("destination_revision", "projection"):
        op.alter_column("notification_deliveries", column, server_default=None)


def _backfill() -> None:
    """Preserve inbox identity/read state; never replay historic external work."""
    op.execute("""
        INSERT INTO notification_destinations
            (id, user_id, provider_id, endpoint_key, kind, channel_context, privacy,
             revision, enabled, active, recovery_allowed, created_at)
        SELECT md5('notification-inbox:' || id::text)::uuid, id, 'core.inbox', 'inbox',
               'inbox', 'internal', 1, 1, true, true, false, EXTRACT(EPOCH FROM now())::bigint
        FROM users
    """)
    op.execute("""
        INSERT INTO notification_receipts (id, user_id, dedupe_key, event_type, source, occurred_at)
        SELECT id, user_id, dedupe_key, 'legacy', 'host', event_at FROM notifications
    """)
    op.execute("""
        INSERT INTO notification_destinations
            (id, user_id, provider_id, endpoint_key, kind, channel_context, privacy,
             revision, enabled, active, recovery_allowed, installation_id, created_at)
        SELECT md5('notification-legacy:' || s.id::text)::uuid, s.user_id, s.provider_id,
               'legacy', 'legacy_webhook', 'external', 0, 1, s.enabled,
               r.revoked_at IS NULL, false, r.installation_id, s.updated_at
        FROM notification_provider_settings s JOIN plugin_notification_provider_registrations r
             ON r.provider_id = s.provider_id
    """)
    op.execute("""
        UPDATE notifications SET required_trust = 2, purpose = 'security', severity = 'warning',
            event_type = 'security.session.anomaly' WHERE kind = 'session_anomaly'
    """)
    op.execute("""
        INSERT INTO notification_deliveries
            (id, notification_id, provider_id, destination_id, destination_revision,
             projection, status, attempts, next_attempt_at)
        SELECT md5('notification-inbox-delivery:' || n.id::text)::uuid, n.id, 'core.inbox',
               d.id, 1, 'canonical', 'sent', 0, n.created_at
        FROM notifications n JOIN notification_destinations d ON d.user_id = n.user_id
        WHERE d.provider_id = 'core.inbox'
    """)
    op.execute("""
        UPDATE notification_deliveries SET
            last_error = CASE WHEN status IN ('pending', 'retry') THEN 'legacy_endpoint_unverified'
                              ELSE last_error END,
            status = CASE WHEN status = 'sent' THEN 'sent' WHEN status = 'failed'
                          THEN 'failed_permanent' ELSE 'suppressed' END
        WHERE provider_id != 'core.inbox'
    """)


def downgrade() -> None:
    # Multiple endpoints cannot be represented by the old schema; fail safely
    # rather than silently discarding delivery history.
    duplicates = op.get_bind().scalar(
        sa.text("""
        SELECT EXISTS (SELECT 1 FROM notification_deliveries WHERE provider_id != 'core.inbox'
                       GROUP BY notification_id, provider_id HAVING COUNT(*) > 1)
    """)
    )
    if duplicates:
        raise RuntimeError("Cannot downgrade notification history with multiple endpoints")
    op.execute("DELETE FROM notification_deliveries WHERE provider_id = 'core.inbox'")
    op.execute("""UPDATE notification_deliveries SET status = CASE
        WHEN status = 'failed_permanent' THEN 'failed'
        WHEN status IN ('retry_wait', 'processing') THEN 'retry'
        WHEN status IN ('suppressed', 'cancelled') THEN 'skipped' ELSE status END""")
    op.execute("DELETE FROM notifications WHERE deleted_at IS NOT NULL")
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "notifications",
        "media_type",
        existing_type=sa.String(length=32),
        type_=sa.VARCHAR(length=10),
        existing_nullable=False,
    )
    op.alter_column(
        "notifications",
        "kind",
        existing_type=sa.String(length=128),
        type_=sa.VARCHAR(length=30),
        existing_nullable=False,
    )
    op.drop_column("notifications", "public_body")
    op.drop_column("notifications", "public_title")
    op.drop_column("notifications", "expires_at")
    op.drop_column("notifications", "deleted_at")
    op.drop_column("notifications", "dismissed_at")
    op.drop_column("notifications", "inbox_visible")
    op.drop_column("notifications", "group_key")
    op.drop_column("notifications", "severity")
    op.drop_column("notifications", "purpose")
    op.drop_column("notifications", "required_trust")
    op.drop_column("notifications", "source")
    op.drop_column("notifications", "event_type")
    op.drop_constraint(
        "fk_notification_delivery_destination", "notification_deliveries", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_notification_delivery_notification_destination",
        "notification_deliveries",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_notification_delivery_notification_provider",
        "notification_deliveries",
        ["notification_id", "provider_id"],
    )
    op.alter_column(
        "notification_deliveries",
        "status",
        existing_type=sa.String(length=32),
        type_=sa.VARCHAR(length=16),
        existing_nullable=False,
    )
    op.drop_column("notification_deliveries", "lease_until")
    op.drop_column("notification_deliveries", "claim_token")
    op.drop_column("notification_deliveries", "projection")
    op.drop_column("notification_deliveries", "destination_revision")
    op.drop_column("notification_deliveries", "destination_id")
    op.drop_table("notification_delivery_attempts")
    op.drop_table("notification_receipts")
    op.drop_table("notification_destinations")
    # ### end Alembic commands ###
