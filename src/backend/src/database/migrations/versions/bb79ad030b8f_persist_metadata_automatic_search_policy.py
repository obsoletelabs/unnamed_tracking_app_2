"""persist metadata automatic search policy

Revision ID: bb79ad030b8f
Revises: e8c64dd34f5c
Create Date: 2026-10-14 08:30:54.253024

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "bb79ad030b8f"
down_revision: Union[str, None] = "e8c64dd34f5c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adopted databases can already contain the current ORM column. Replaying
    # this revision must preserve their policy and every existing setting.
    if h.has_column("app_integration_settings", "metadata_automatic_search_policy"):
        return
    op.add_column(
        "app_integration_settings",
        sa.Column(
            "metadata_automatic_search_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    if h.has_column("app_integration_settings", "metadata_automatic_search_policy"):
        op.drop_column("app_integration_settings", "metadata_automatic_search_policy")
