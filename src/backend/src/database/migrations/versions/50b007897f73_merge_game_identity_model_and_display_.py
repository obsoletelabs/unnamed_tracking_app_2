"""merge game identity model and display name heads

Revision ID: 50b007897f73
Revises: 716a1e2c451d, a7d3c91e5b20
Create Date: 2026-10-17 09:43:39.588478

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "50b007897f73"
down_revision: Union[str, Sequence[str], None] = ("716a1e2c451d", "a7d3c91e5b20")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge the two heads; no schema change."""


def downgrade() -> None:
    """Split back into the two parent heads; no schema change."""
