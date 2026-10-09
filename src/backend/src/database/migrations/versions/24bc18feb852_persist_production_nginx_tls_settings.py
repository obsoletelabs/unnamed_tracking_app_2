"""Persist production Nginx TLS settings.

Revision ID: 24bc18feb852
Revises: a4456747e462
Create Date: 2026-10-09 09:36:40.084728
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "24bc18feb852"
down_revision: Union[str, None] = "a4456747e462"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = "app_integration_settings"
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
    for name, column_type in (
        ("nginx_tls_enabled", sa.Boolean()),
        ("nginx_tls_redirect_http", sa.Boolean()),
        ("nginx_tls_certificate", sa.String(512)),
        ("nginx_tls_private_key", sa.String(512)),
    ):
        if name not in existing:
            op.add_column(table, sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    for name in (
        "nginx_tls_private_key",
        "nginx_tls_certificate",
        "nginx_tls_redirect_http",
        "nginx_tls_enabled",
    ):
        op.drop_column("app_integration_settings", name)
