"""Completion-time columns upgrade populated libraries without changing existing playtime."""

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.database.models.game import Game
from src.database.models.user import User


def test_time_categories_upgrade_replay_and_downgrade_preserve_main_and_playtime():
    schema = "time_categories_" + uuid4().hex
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
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", "fed12ab64423")
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            user_id, game_id = uuid4(), uuid4()
            engine = create_engine(scoped.set(drivername="postgresql+psycopg"))
            try:
                with engine.begin() as db:
                    db.execute(
                        User.__table__.insert().values(
                            id=user_id,
                            username=schema,
                            email=schema + "@example.test",
                            password_hash="x",
                        )
                    )
                    db.execute(
                        Game.__table__.insert().values(
                            id=game_id,
                            user_id=user_id,
                            title="Existing",
                            sort_title="existing",
                            folder_location="Existing",
                            time_to_beat_hours=Decimal("12.3"),
                            playtime_seconds=1234,
                        )
                    )
            finally:
                engine.dispose()
            migrate("upgrade", "head")
            statement = (
                "SELECT time_to_beat_hours,time_to_beat_extras_hours,"
                "time_to_beat_completionist_hours,time_to_beat_all_hours,playtime_seconds FROM games"
            )
            assert connection.execute(statement).fetchone() == (
                Decimal("12.3"),
                None,
                None,
                None,
                1234,
            )
            connection.execute(
                "UPDATE games SET time_to_beat_extras_hours=16.2,"
                "time_to_beat_completionist_hours=25.8,time_to_beat_all_hours=18.1"
            )
            migrate("stamp", "fed12ab64423")
            migrate("upgrade", "head")
            assert connection.execute(statement).fetchone() == (
                Decimal("12.3"),
                Decimal("16.2"),
                Decimal("25.8"),
                Decimal("18.1"),
                1234,
            )
            migrate("downgrade", "fed12ab64423")
            assert connection.execute(
                "SELECT time_to_beat_hours,playtime_seconds FROM games"
            ).fetchone() == (Decimal("12.3"), 1234)
            migrate("upgrade", "head")
            assert connection.execute(statement).fetchone() == (
                Decimal("12.3"),
                None,
                None,
                None,
                1234,
            )
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
