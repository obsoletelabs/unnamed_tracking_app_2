"""The game identity migration (#93) is additive, replayable and safe on populated libraries."""

import importlib
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.database.base import Base

BASELINE = "c10098e58426"
PREVIOUS_HEAD = "e4a0157eef4b"
IDENTITY = "716a1e2c451d"
NEW_TABLES = {
    "game_umbrellas",
    "game_identities",
    "game_source_connections",
    "game_source_observations",
    "game_source_observation_revisions",
    "game_source_observation_links",
    "game_identity_decisions",
    "game_relationships",
    "game_metric_counters",
    "game_metric_counter_members",
    "game_metric_adjustments",
    "game_field_values",
    "game_manual_achievements",
}
RETAINED = {
    "cards",
    "sets",
    "bounties",
    "bounty_objectives",
    "bounty_evidence",
    "bounty_journal_entries",
    "bounty_point_transactions",
}


def test_identity_migration_follows_the_previous_head_and_adds_only_tables():
    module = importlib.import_module(
        "src.database.migrations.versions.716a1e2c451d_add_canonical_game_identity_model"
    )
    assert module.down_revision == PREVIOUS_HEAD
    assert set(module.NEW_TABLES) == NEW_TABLES
    source = Path(module.__file__).read_text(encoding="utf-8")
    upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    for forbidden in ("drop_table", "drop_column", "drop_index", "drop_constraint", "alter_column"):
        assert forbidden not in upgrade


def test_metadata_matches_the_migrated_database_and_proposes_no_drops():
    """Autogenerate against the migrated test database finds nothing to change,
    and in particular never proposes dropping existing tables, indexes or constraints."""
    import pkgutil

    from src.database import models

    for module in pkgutil.iter_modules(models.__path__):  # as migrations/env.py does
        importlib.import_module(f"src.database.models.{module.name}")

    engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as conn:

            def include(object_, name, type_, reflected, compare_to):
                # the same filter as migrations/env.py for the retained legacy tables
                return not (
                    type_ == "table" and reflected and compare_to is None and name in RETAINED
                )

            context = MigrationContext.configure(conn, opts={"include_object": include})
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


def test_populated_upgrade_replay_and_guarded_downgrade():
    schema = "identity_model_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
    url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    scoped = base.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)

    def migrate(*arguments, expect=0):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
            cwd=Path(__file__).parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert (result.returncode == 0) == (expect == 0), result.stdout + result.stderr
        return result.stdout + result.stderr

    user_id, other_id, steam_id, playnite_id, parent_id = [uuid4() for _ in range(5)]
    snapshot = (
        "SELECT id, user_id, title, source, external_id, playnite_guid, parent_game_id, "
        "relationship_type, playtime_seconds, status, locked_fields FROM games ORDER BY id"
    )
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", BASELINE)
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for uid in (user_id, other_id):
                connection.execute(
                    "INSERT INTO users (id, username, email, password_hash, is_active, is_admin,"
                    " created_at, updated_at) VALUES (%s, %s, %s, 'x', true, false, 1, 1)",
                    (uid, uid.hex, f"{uid}@example.test"),
                )
            games = [
                (parent_id, user_id, "Minecraft", "Steam", "1672970", None, None, None, 36000),
                (
                    steam_id,
                    user_id,
                    "Minecraft Java",
                    "Steam",
                    "570",
                    None,
                    parent_id,
                    "owned_copy",
                    7200,
                ),
                (playnite_id, user_id, "Minecraft Bedrock", None, None, uuid4(), None, None, 100),
                (uuid4(), other_id, "Minecraft", "Steam", "1672970", None, None, None, 5),
            ]
            for game_id, uid, title, source, external, guid, parent, kind, played in games:
                connection.execute(
                    "INSERT INTO games (id, user_id, folder_location, title, sort_title, source,"
                    " external_id, playnite_guid, parent_game_id, relationship_type,"
                    " playtime_seconds, status, locked_fields, provider_ids, tags, features,"
                    " collections, profiles_enabled, osrs_stats_enabled, favorite, created_at,"
                    " updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'PLAYING',"
                    " ARRAY['title'], '{}', '{}', '{}', '{}', false, false, false, 1, 1)",
                    (
                        game_id,
                        uid,
                        game_id.hex,
                        title,
                        title.lower(),
                        source,
                        external,
                        guid,
                        parent,
                        kind,
                        played,
                    ),
                )
            connection.execute(
                "INSERT INTO game_duplicate_dismissals (user_id, first_game_id, second_game_id,"
                " created_at) VALUES (%s, %s, %s, 1)",
                (user_id, *sorted([steam_id, playnite_id])),
            )
            before = connection.execute(snapshot).fetchall()
            dismissals = connection.execute("SELECT * FROM game_duplicate_dismissals").fetchall()

            migrate("upgrade", "head")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                    (schema,),
                )
            }
            assert NEW_TABLES <= tables
            assert connection.execute(snapshot).fetchall() == before
            assert (
                connection.execute("SELECT * FROM game_duplicate_dismissals").fetchall()
                == dismissals
            )
            for table in NEW_TABLES:
                assert connection.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                ).fetchone() == (0,)

            # replaying the revision on an already-upgraded database changes nothing
            migrate("stamp", BASELINE)
            migrate("upgrade", "head")
            assert connection.execute(snapshot).fetchall() == before

            # an empty identity schema downgrades cleanly, and games are untouched
            migrate("downgrade", BASELINE)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                    (schema,),
                )
            }
            assert not NEW_TABLES & tables
            assert connection.execute(snapshot).fetchall() == before
            migrate("upgrade", "head")

            # once the model holds user decisions, downgrade refuses instead of dropping them
            connection.execute(
                "INSERT INTO game_umbrellas (id, user_id, title, sort_title, created_at, updated_at)"
                " VALUES (%s, %s, 'Minecraft', 'minecraft', 1, 1)",
                (uuid4(), user_id),
            )
            output = migrate("downgrade", BASELINE, expect=1)
            assert "game_umbrellas" in output
            assert connection.execute("SELECT count(*) FROM game_umbrellas").fetchone() == (1,)
            assert connection.execute(snapshot).fetchall() == before
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
