"""Populated upgrades and adopted filename columns retain data through replay."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.database.models.game import Game
from src.database.models.game_file_item import GameFileItem
from src.database.models.inbox_item import InboxItem
from src.database.models.media_item import MediaItem
from src.database.models.user import User


def test_original_filename_upgrade_replay_and_downgrade_preserve_existing_files():
    schema = "filename_migration_" + uuid4().hex
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
            migrate("upgrade", "0e3353138981")
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
                            sort_title="Existing",
                            folder_location="Existing",
                        )
                    )
                    for model, owner, owner_id, kind in (
                        (MediaItem, "game_id", game_id, "screenshot"),
                        (InboxItem, "user_id", user_id, "screenshot"),
                        (GameFileItem, "game_id", game_id, "doc"),
                    ):
                        db.execute(
                            model.__table__.insert().values(
                                id=uuid4(),
                                **{owner: owner_id},
                                kind=kind,
                                filename="abcdef12_existing.png",
                                created_at=1,
                            )
                        )
            finally:
                engine.dispose()
            entries = (
                ("media_items", "game_id", game_id, "screenshot"),
                ("inbox_items", "user_id", user_id, "screenshot"),
                ("game_file_items", "game_id", game_id, "doc"),
            )
            migrate("upgrade", "head")
            for table, *_rest in entries:
                statement = sql.SQL("SELECT filename,original_filename FROM {}").format(
                    sql.Identifier(table)
                )
                assert connection.execute(statement).fetchone() == ("abcdef12_existing.png", None)
                connection.execute(
                    sql.SQL("UPDATE {} SET original_filename=%s").format(sql.Identifier(table)),
                    ("東京 — original.png",),
                )
            migrate("stamp", "0e3353138981")
            migrate("upgrade", "head")
            for table, *_rest in entries:
                assert connection.execute(
                    sql.SQL("SELECT original_filename FROM {}").format(sql.Identifier(table))
                ).fetchone() == ("東京 — original.png",)
            migrate("downgrade", "0e3353138981")
            for table, *_rest in entries:
                assert connection.execute(
                    sql.SQL("SELECT filename FROM {}").format(sql.Identifier(table))
                ).fetchone() == ("abcdef12_existing.png",)
            migrate("upgrade", "head")
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
