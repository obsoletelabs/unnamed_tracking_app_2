"""Add public application URL for notification links.

Revision ID: ef1fa87c9382
Revises: efc50aae7b57
Create Date: 2026-10-09 08:24:08.195351
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ef1fa87c9382"
down_revision: Union[str, None] = "efc50aae7b57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Startup adoption may encounter a complete current schema. Never replace its URL.
    columns = sa.inspect(op.get_bind()).get_columns("app_integration_settings")
    if any(column["name"] == "public_app_url" for column in columns):
        return
    op.add_column(
        "app_integration_settings", sa.Column("public_app_url", sa.String(2048), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("app_integration_settings", "public_app_url")
