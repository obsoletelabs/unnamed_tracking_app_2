"""set default built-in presentation

Revision ID: 83bb783fd35e
Revises: bb79ad030b8f
Create Date: 2026-10-14 09:34:11.470947

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "83bb783fd35e"
down_revision: Union[str, None] = "bb79ad030b8f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    h.add_column_if_missing(
        "app_integration_settings", sa.Column("default_ui_style", sa.String(16), nullable=True)
    )


def downgrade() -> None:
    if h.has_column("app_integration_settings", "default_ui_style"):
        op.drop_column("app_integration_settings", "default_ui_style")
