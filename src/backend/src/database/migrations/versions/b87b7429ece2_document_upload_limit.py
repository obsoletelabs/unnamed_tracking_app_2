"""document upload limit

Revision ID: b87b7429ece2
Revises: c10098e58426
Create Date: 2026-10-16 21:14:57.446251

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "b87b7429ece2"
down_revision: Union[str, None] = "c10098e58426"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Docs get their own cap; null means use the MAX_DOC_SIZE_MB environment default."""
    h.add_column_if_missing(
        "app_integration_settings", sa.Column("max_doc_size_mb", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    """Remove only the new override; every other limit and setting is kept."""
    op.execute("ALTER TABLE app_integration_settings DROP COLUMN IF EXISTS max_doc_size_mb")
