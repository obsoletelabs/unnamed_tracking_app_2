"""keep metadata values that lost a refresh's merge as field value candidates

A field preference names a metadata provider. A refresh recorded only the values that won the
merge, so a provider whose value lost (RAWG's developer behind IGDB's) had nothing a preference
could apply (D2). Such values are now kept with the state ``candidate``: one row per game,
field and provider label.

Revision ID: b8d2f6a41c93
Revises: a7c3e91f5b20
Create Date: 2026-11-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "b8d2f6a41c93"
down_revision: Union[str, None] = "a7c3e91f5b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "game_field_values"
_STATE = "ck_game_field_values_state"
_INDEX = "uq_game_field_values_candidate"
_NEW = "state IN ('current', 'proposed', 'superseded', 'rejected', 'candidate')"
_OLD = "state IN ('current', 'proposed', 'superseded', 'rejected')"


def upgrade() -> None:
    if h.has_constraint(_TABLE, _STATE):
        op.drop_constraint(_STATE, _TABLE, type_="check")
    op.create_check_constraint(_STATE, _TABLE, _NEW)
    h.create_index_if_missing(
        _INDEX,
        _TABLE,
        ["game_id", "field", "source_label"],
        unique=True,
        postgresql_where=sa.text("state = 'candidate'"),
    )


def downgrade() -> None:
    # candidates are derived data: the next refresh records them again
    op.execute(f"DELETE FROM {_TABLE} WHERE state = 'candidate'")  # noqa: S608
    h.drop_index_if_exists(_INDEX, _TABLE)
    op.drop_constraint(_STATE, _TABLE, type_="check")
    op.create_check_constraint(_STATE, _TABLE, _OLD)
