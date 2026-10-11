"""Scope personal plugin schedules to their owning users.

Revision ID: 626d06554967
Revises: f1b4375a624c
"""

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

revision = "626d06554967"
down_revision = "f1b4375a624c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.add_column_if_missing("job_settings", sa.Column("user_id", sa.UUID(), nullable=True))
    h.create_index_if_missing("ix_job_settings_user_id", "job_settings", ["user_id"])
    # Check this relation in the current schema, including during adoption.
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys("job_settings")
    if not any(key["name"] == "fk_job_settings_user_id" for key in foreign_keys):
        op.create_foreign_key(
            "fk_job_settings_user_id", "job_settings", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    op.drop_constraint("fk_job_settings_user_id", "job_settings", type_="foreignkey")
    op.drop_index("ix_job_settings_user_id", table_name="job_settings")
    op.drop_column("job_settings", "user_id")
