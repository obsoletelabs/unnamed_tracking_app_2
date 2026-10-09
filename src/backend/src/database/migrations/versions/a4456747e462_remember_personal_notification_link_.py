"""Remember personal notification link origins.

Revision ID: a4456747e462
Revises: ef1fa87c9382
Create Date: 2026-10-09 09:03:47.163400
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4456747e462"
down_revision: Union[str, None] = "ef1fa87c9382"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adoption can start from a complete current model; preserve existing owner URLs.
    for table, name in (
        ("notification_destinations", "notification_url"),
        ("users", "last_app_url"),
    ):
        columns = sa.inspect(op.get_bind()).get_columns(table)
        if not any(column["name"] == name for column in columns):
            op.add_column(table, sa.Column(name, sa.String(2048), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_app_url")
    op.drop_column("notification_destinations", "notification_url")
