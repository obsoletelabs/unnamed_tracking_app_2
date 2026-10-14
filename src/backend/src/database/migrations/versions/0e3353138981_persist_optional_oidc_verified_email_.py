"""persist optional OIDC verified email policy

Revision ID: 0e3353138981
Revises: 83bb783fd35e
Create Date: 2026-10-14 12:40:25.227644

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "0e3353138981"
down_revision: Union[str, None] = "83bb783fd35e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A server-side false default preserves existing providers and raw-SQL inserts.
    h.add_column_if_missing(
        "oidc_settings",
        sa.Column(
            "require_verified_email", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    if h.has_column("oidc_settings", "require_verified_email"):
        op.drop_column("oidc_settings", "require_verified_email")
