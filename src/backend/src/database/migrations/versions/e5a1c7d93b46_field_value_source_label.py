"""name the metadata provider on field values

Revision ID: e5a1c7d93b46
Revises: ab6f42c8d91e
Create Date: 2026-10-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "e5a1c7d93b46"
down_revision: Union[str, Sequence[str], None] = "ab6f42c8d91e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "game_field_values"
_NAME = "ck_game_field_values_observation"
_NEW = (
    "(origin <> 'source' OR observation_id IS NOT NULL)"
    " AND (origin <> 'metadata' OR observation_id IS NOT NULL OR source_label IS NOT NULL)"
    " AND (origin <> 'user' OR (observation_id IS NULL AND source_label IS NULL))"
)
_OLD = (
    "(origin NOT IN ('source', 'metadata') OR observation_id IS NOT NULL)"
    " AND (origin <> 'user' OR observation_id IS NULL)"
)


def upgrade() -> None:
    h.add_column_if_missing(_TABLE, sa.Column("source_label", sa.String(length=64), nullable=True))
    if h.has_constraint(_TABLE, _NAME):
        op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(_NAME, _TABLE, _NEW)


def downgrade() -> None:
    # a metadata value that names only its provider cannot satisfy the older rule
    op.execute(
        f"DELETE FROM {_TABLE} WHERE origin = 'metadata' AND observation_id IS NULL"  # noqa: S608
    )
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(_NAME, _TABLE, _OLD)
    op.drop_column(_TABLE, "source_label")
