"""widen legacy Steam API keys for encryption at rest

Revision ID: c10098e58426
Revises: 03bb35a3b692
Create Date: 2026-10-15 19:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c10098e58426"
down_revision: Union[str, None] = "03bb35a3b692"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Encrypted keys no longer fit 64 characters. Existing plain keys are kept and
    # encrypted by the application when next used; widening is safe to repeat.
    op.alter_column(
        "users",
        "steam_api_key",
        existing_type=sa.String(length=64),
        type_=sa.String(length=512),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Encrypted keys cannot be narrowed back to 64 characters without losing them,
    # so the wider column is retained.
    pass
