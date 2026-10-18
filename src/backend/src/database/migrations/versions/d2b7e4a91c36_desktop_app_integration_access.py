"""Let first-party desktop apps use integration access.

Revision ID: d2b7e4a91c36
Revises: c91f3a27d5b8
Create Date: 2026-10-18 04:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

revision: str = "d2b7e4a91c36"
down_revision: Union[str, None] = "c91f3a27d5b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing clients are web origins; existing instances allow desktop apps, which
    # replace the API keys their users can already create. Safe to repeat.
    h.add_column_if_missing(
        "integration_clients",
        sa.Column("kind", sa.String(length=16), server_default="web", nullable=False),
    )
    h.add_column_if_missing(
        "integration_policy",
        sa.Column("allow_desktop_apps", sa.Boolean(), server_default="true", nullable=False),
    )


def downgrade() -> None:
    # Older versions cannot tell a desktop client from a web origin, so remove them
    # (and, by cascade, their requests and grants) before dropping the column.
    if h.has_column("integration_clients", "kind"):
        op.execute("DELETE FROM integration_clients WHERE kind = 'desktop'")
        op.drop_column("integration_clients", "kind")
    if h.has_column("integration_policy", "allow_desktop_apps"):
        op.drop_column("integration_policy", "allow_desktop_apps")
