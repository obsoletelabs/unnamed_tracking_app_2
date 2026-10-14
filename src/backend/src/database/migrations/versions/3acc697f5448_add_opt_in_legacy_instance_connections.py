"""add opt in legacy instance connections

Revision ID: 3acc697f5448
Revises: 03402febb5ae
Create Date: 2026-10-13 14:35:43.710241

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "3acc697f5448"
down_revision: Union[str, None] = "03402febb5ae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adoption replays migrations on existing schemas; preserve existing data.
    h.add_column_if_missing(
        "integration_policy",
        sa.Column("allow_legacy_api_keys", sa.Boolean(), server_default="false", nullable=False),
    )
    h.add_column_if_missing(
        "public_app_sources",
        sa.Column(
            "credential_kind", sa.String(length=16), server_default="delegated", nullable=False
        ),
    )
    h.add_column_if_missing(
        "public_app_sources", sa.Column("remote_user_id", sa.String(length=64), nullable=True)
    )
    op.alter_column("public_app_sources", "request_id", existing_type=sa.UUID(), nullable=True)


def downgrade() -> None:
    # An older host cannot represent or safely use legacy connections.
    op.execute(sa.text("DELETE FROM public_app_sources WHERE credential_kind = 'legacy_api_key'"))
    op.alter_column("public_app_sources", "request_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("public_app_sources", "remote_user_id")
    op.drop_column("public_app_sources", "credential_kind")
    op.drop_column("integration_policy", "allow_legacy_api_keys")
