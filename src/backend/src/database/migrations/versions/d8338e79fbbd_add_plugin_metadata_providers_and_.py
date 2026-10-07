"""Add plugin metadata providers, scoped configuration and persistent game identities.

Revision ID: d8338e79fbbd
Revises: 65acf36995e5

Generated with Alembic and reviewed: unrelated note-table drops were removed.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d8338e79fbbd"
down_revision = "65acf36995e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("plugin_metadata_provider_registrations"):
        op.create_table(
            "plugin_metadata_provider_registrations",
            sa.Column("provider_id", sa.String(128), primary_key=True),
            sa.Column("plugin_id", sa.String(128), nullable=False),
            sa.Column("installation_id", sa.UUID(), nullable=False),
            sa.Column("declaration", postgresql.JSONB(), nullable=False),
            sa.Column("registered_at", sa.BigInteger(), nullable=False),
            sa.Column("revoked_at", sa.BigInteger()),
        )
    indexes = {item["name"] for item in inspector.get_indexes("plugin_metadata_provider_registrations")}
    if "ix_plugin_metadata_provider_registrations_plugin_id" not in indexes:
        op.create_index("ix_plugin_metadata_provider_registrations_plugin_id",
                        "plugin_metadata_provider_registrations", ["plugin_id"])
    if not inspector.has_table("plugin_provider_configurations"):
        op.create_table(
            "plugin_provider_configurations",
            sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("provider_id", sa.String(128), nullable=False),
            sa.Column("plugin_id", sa.String(128), nullable=False),
            sa.Column("scope", sa.String(36), nullable=False),
            sa.Column("encrypted_values", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.BigInteger(), nullable=False),
            sa.UniqueConstraint("provider_id", "scope", name="uq_plugin_provider_configuration_scope"),
        )
    indexes = {item["name"] for item in inspector.get_indexes("plugin_provider_configurations")}
    if "ix_plugin_provider_configurations_provider_id" not in indexes:
        op.create_index("ix_plugin_provider_configurations_provider_id",
                        "plugin_provider_configurations", ["provider_id"])
    if "provider_ids" not in {column["name"] for column in inspector.get_columns("games")}:
        op.add_column("games", sa.Column("provider_ids", postgresql.JSONB(), nullable=False,
                                        server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("games", "provider_ids")
    op.drop_index("ix_plugin_provider_configurations_provider_id",
                  table_name="plugin_provider_configurations")
    op.drop_table("plugin_provider_configurations")
    op.drop_index("ix_plugin_metadata_provider_registrations_plugin_id",
                  table_name="plugin_metadata_provider_registrations")
    op.drop_table("plugin_metadata_provider_registrations")
