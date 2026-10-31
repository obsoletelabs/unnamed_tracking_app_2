"""delete a game's links with the game at the database level

Revision ID: b7d3a9e14c52
Revises: 9cc6333e6de6
Create Date: 2026-10-31 09:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "b7d3a9e14c52"
down_revision: Union[str, None] = "9cc6333e6de6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK_NAME = "game_links_game_id_fkey"


def _game_foreign_keys() -> list[dict]:
    """The foreign keys from game_links.game_id to games.id, with their current rule."""
    if not h.has_table("game_links"):
        return []
    return [
        fk
        for fk in sa.inspect(op.get_bind()).get_foreign_keys("game_links")
        if fk["constrained_columns"] == ["game_id"] and fk["referred_table"] == "games"
    ]


def _replace_foreign_key(ondelete: str | None) -> None:
    keys = _game_foreign_keys()
    if len(keys) == 1 and (keys[0].get("options", {}).get("ondelete") or None) == ondelete:
        return
    # Dropping and re-adding runs in the migration transaction; no row is touched, and the
    # re-added constraint is validated against the existing rows (none can be orphaned,
    # because the old constraint already enforced the same parent).
    for fk in keys:
        op.drop_constraint(fk["name"], "game_links", type_="foreignkey")
    op.create_foreign_key(_FK_NAME, "game_links", "games", ["game_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    # A game owns its links. The ORM already deletes them when a game is deleted through the
    # session, but a database-level delete (an account deletion cascading users -> games, or any
    # direct delete) hit the constraint and failed. The rule only removes links whose own game
    # is being removed; nothing else cascades from game_links.
    _replace_foreign_key("CASCADE")


def downgrade() -> None:
    # Back to the earlier rule (no action). Rows are untouched.
    _replace_foreign_key(None)
