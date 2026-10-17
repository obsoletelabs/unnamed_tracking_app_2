"""merge game identity listing and notification story lifecycle heads

Revision ID: 9d4b2e6f1a35
Revises: 6f75008c02a6, c4e8a1f6b3d7
Create Date: 2026-10-17 17:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "9d4b2e6f1a35"
down_revision: Union[str, Sequence[str], None] = ("6f75008c02a6", "c4e8a1f6b3d7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge the two heads; no schema change."""


def downgrade() -> None:
    """Split back into the two parent heads; no schema change."""
