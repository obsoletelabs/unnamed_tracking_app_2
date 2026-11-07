"""Migration b8d2f6a41c93 (field value candidates, D2) on PostgreSQL: upgrade, downgrade, upgrade.

The upgrade lets `game_field_values` hold a provider's value that lost a refresh's merge
(state `candidate`, one per game, field and provider). The downgrade removes those derived rows
so the older state rule can be restored. The test migrates a schema of its own."""

import os
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings
from tests.test_plugin_api_1120_migration import _owner_url, _run_alembic, _seed_library

BEFORE = "a7c3e91f5b20"
REVISION = "b8d2f6a41c93"


def _insert(connection: psycopg.Connection, user_id, game_id, state: str, label: str) -> None:
    connection.execute(
        "INSERT INTO game_field_values (id, user_id, game_id, field, value, state, origin, "
        "source_label, recorded_at, locked) "
        "VALUES (%s, %s, %s, 'developer', '\"Valve (RAWG)\"', %s, 'metadata', %s, 1, false)",
        (uuid4(), user_id, game_id, state, label),
    )


def _refused(connection: psycopg.Connection, error: type[Exception], *args) -> None:
    try:
        _insert(connection, *args)
    except error:
        return
    raise AssertionError(f"insert {args!r} was accepted")


def test_field_value_candidates_upgrade_downgrade_and_upgrade_again():
    schema = f"d2_cand_{uuid4().hex}"
    scoped = make_url(settings.DATABASE_URL).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)

    with psycopg.connect(_owner_url(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            _run_alembic(environment, "upgrade", BEFORE)
            user_id, games = _seed_library(scoped)
            game_id = next(iter(games.values()))
            # before the revision a candidate is not a state
            _refused(connection, psycopg.errors.CheckViolation, user_id, game_id, "candidate", "R")

            _run_alembic(environment, "upgrade", REVISION)
            _insert(connection, user_id, game_id, "candidate", "RAWG")
            _insert(connection, user_id, game_id, "candidate", "Atlas")
            _insert(connection, user_id, game_id, "current", "IGDB")
            # one candidate per game, field and provider
            _refused(
                connection, psycopg.errors.UniqueViolation, user_id, game_id, "candidate", "RAWG"
            )

            _run_alembic(environment, "downgrade", BEFORE)
            states = connection.execute(
                "SELECT state, source_label FROM game_field_values ORDER BY source_label"
            ).fetchall()
            # candidates are derived data and go; the game's own value stays
            assert states == [("current", "IGDB")]
            _refused(connection, psycopg.errors.CheckViolation, user_id, game_id, "candidate", "R")

            _run_alembic(environment, "upgrade", "head")
            _insert(connection, user_id, game_id, "candidate", "RAWG")
            # the upgrade is safe to run over its own result
            connection.execute("UPDATE alembic_version SET version_num = %s", (BEFORE,))
            _run_alembic(environment, "upgrade", REVISION)
            assert connection.execute(
                "SELECT count(*) FROM game_field_values WHERE state = 'candidate'"
            ).fetchone() == (1,)
        finally:
            connection.execute(sql.SQL("RESET search_path"))
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
