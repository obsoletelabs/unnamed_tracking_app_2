"""#260: deleting a movie or TV show must not scan the anime table (measured bottleneck)."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings

INDEXES = {
    "ix_anime_linked_movie_id": "linked_movie_id",
    "ix_anime_linked_tv_show_id": "linked_tv_show_id",
}


def indexes(connection):
    rows = connection.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'anime' AND indexname LIKE 'ix_anime_linked_%'"
    ).fetchall()
    return {name: definition for name, definition in rows}


def test_upgrade_adds_partial_indexes_replay_is_safe_and_downgrade_removes_them():
    schema = "anime_links_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
    url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    scoped = base.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)

    def migrate(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
            cwd=Path(__file__).parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", "d2b7e4a91c36")
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            assert indexes(connection) == {}
            migrate("upgrade", "head")
            found = indexes(connection)
            assert set(found) == set(INDEXES)
            for name, column in INDEXES.items():
                assert (
                    f"({column})" in found[name] and f"WHERE ({column} IS NOT NULL)" in found[name]
                )
            migrate(
                "stamp", "d2b7e4a91c36"
            )  # a replayed upgrade keeps working on a database that has them
            migrate("upgrade", "head")
            assert set(indexes(connection)) == set(INDEXES)
            migrate("downgrade", "d2b7e4a91c36")
            assert indexes(connection) == {}
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_models_declare_the_same_indexes():
    from src.database.models.anime import Anime

    declared = {index.name for index in Anime.__table__.indexes}
    assert set(INDEXES) <= declared
