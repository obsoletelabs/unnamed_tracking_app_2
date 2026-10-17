"""Index the live library listing.

Revision ID: c4e8a1f6b3d7
Revises: a3c5e7b9d102
Create Date: 2026-10-17 12:00:00.000000

Final acceptance of the game identity overhaul (#103). Profiling a 20,000-game library
showed the library list (`WHERE user_id = ? AND deleted_at IS NULL ORDER BY sort_title
LIMIT 200 OFFSET ?`) reading and sorting every row of the user for each page. A partial
index on (user_id, sort_title, id) returns a page without that sort. The migration is
additive and only creates the index (skipped if it already exists); nothing is altered
or dropped. The other hot queries already used indexes (see the acceptance report).
"""

from sqlalchemy import text

from src.database import migration_helpers as h

revision: str = "c4e8a1f6b3d7"
down_revision: str | None = "a3c5e7b9d102"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    h.create_index_if_missing(
        "ix_games_user_sort_title_live",
        "games",
        ["user_id", "sort_title", "id"],
        postgresql_where=text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    h.drop_index_if_exists("ix_games_user_sort_title_live", "games")
