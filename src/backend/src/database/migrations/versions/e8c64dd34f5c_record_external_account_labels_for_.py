"""record external account labels for integration consent

Revision ID: e8c64dd34f5c
Revises: da9e271523e2
Create Date: 2026-10-14 08:38:20.878658

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "e8c64dd34f5c"
down_revision: Union[str, None] = "da9e271523e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable additions preserve existing grants and external identities.
    h.add_column_if_missing(
        "delegation_requests",
        sa.Column("external_account", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    h.add_column_if_missing(
        "public_app_actors", sa.Column("account_name", sa.String(length=100), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("public_app_actors", "account_name")
    op.drop_column("delegation_requests", "external_account")
