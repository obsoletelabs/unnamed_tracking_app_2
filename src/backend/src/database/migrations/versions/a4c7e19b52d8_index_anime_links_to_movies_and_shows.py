"""Index the anime links to movies and TV shows so deleting a title does not scan the anime table (#260).

Revision ID: a4c7e19b52d8
Revises: d2b7e4a91c36
"""

import sqlalchemy as sa

from src.database import migration_helpers as h

revision = "a4c7e19b52d8"
down_revision = "d2b7e4a91c36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    h.create_index_if_missing(
        "ix_anime_linked_movie_id",
        "anime",
        ["linked_movie_id"],
        postgresql_where=sa.text("linked_movie_id IS NOT NULL"),
    )
    h.create_index_if_missing(
        "ix_anime_linked_tv_show_id",
        "anime",
        ["linked_tv_show_id"],
        postgresql_where=sa.text("linked_tv_show_id IS NOT NULL"),
    )


def downgrade() -> None:
    h.drop_index_if_exists("ix_anime_linked_tv_show_id", "anime")
    h.drop_index_if_exists("ix_anime_linked_movie_id", "anime")
