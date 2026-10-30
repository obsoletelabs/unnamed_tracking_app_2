"""The tag catalogue migration is additive: stored tags survive upgrade, downgrade and re-upgrade."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.database.models.game import Game
from src.database.models.user import User

PREVIOUS_HEAD = "f3b8d1a6c274"
CATALOGUE_TABLES = {
    "user_tags",
    "user_tag_aliases",
    "user_tag_suppressions",
    "default_tags",
    "default_tag_aliases",
}
TAGS = ["Roguelike", "roguelite", "Genre: Action", "日本語", "  odd  spacing "]


def test_catalogue_migration_never_touches_stored_tags():
    schema = "tag_catalogue_migration_" + uuid4().hex
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
        engine = create_engine(scoped.set(drivername="postgresql+psycopg"))
        try:
            migrate("upgrade", PREVIOUS_HEAD)
            user_id, game_id = uuid4(), uuid4()
            with engine.begin() as db:
                db.execute(
                    User.__table__.insert().values(
                        id=user_id,
                        username=schema[-40:],
                        email=schema[-30:] + "@example.test",
                        password_hash="x",
                    )
                )
                db.execute(
                    Game.__table__.insert().values(
                        id=game_id,
                        user_id=user_id,
                        title="Tagged",
                        sort_title="tagged",
                        folder_location="Tagged",
                        tags=TAGS,
                    )
                )

            def stored_tags():
                with engine.connect() as db:
                    return db.scalar(select(Game.__table__.c.tags).where(Game.id == game_id))

            def tables():
                return CATALOGUE_TABLES & set(inspect(engine).get_table_names())

            assert tables() == set()
            migrate("upgrade", "head")
            assert tables() == CATALOGUE_TABLES
            assert stored_tags() == TAGS
            migrate("downgrade", PREVIOUS_HEAD)
            assert tables() == set()
            assert stored_tags() == TAGS
            migrate("upgrade", "head")
            assert tables() == CATALOGUE_TABLES
            assert stored_tags() == TAGS
        finally:
            engine.dispose()
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
