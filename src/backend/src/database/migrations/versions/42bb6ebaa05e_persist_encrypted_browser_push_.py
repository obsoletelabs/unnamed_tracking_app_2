"""Persist encrypted browser push configuration.

Revision ID: 42bb6ebaa05e
Revises: a1a6b04b3606
Create Date: 2026-10-10 00:19:48.826008

Generated with Compose/Alembic and reviewed. Historical game-note tables
are deliberately preserved; they are unrelated to this additive change.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "42bb6ebaa05e"
down_revision: str | None = "a1a6b04b3606"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("app_integration_settings")
    }
    for name, column_type in (
        ("web_push_vapid_subject", sa.String(length=1024)),
        ("web_push_vapid_private_key", sa.Text()),
    ):
        # Adopt metadata-created databases using the normal safe-rerun pattern.
        if name not in columns:
            op.add_column("app_integration_settings", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    configured = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM app_integration_settings "
            "WHERE nullif(web_push_vapid_private_key, '') IS NOT NULL"
        )
    )
    if configured:
        raise RuntimeError("Clear the browser push private key before downgrading")
    op.drop_column("app_integration_settings", "web_push_vapid_private_key")
    op.drop_column("app_integration_settings", "web_push_vapid_subject")
