"""Migration constraint checks look at the relation the next statement targets.

An isolated-schema upgrade or adoption can run next to an application schema
that already has identically named constraints. Those must not count as proof
that the target relation has them, or the target is left without its
constraint and its downgrade fails (#81).
"""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, insert, select, text
from sqlalchemy.engine import URL, make_url

from src.core.config import settings
from src.database import migration_helpers as h
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow
from src.database.models.user import User

_BACKEND = Path(__file__).parents[1]
_BASELINE = "f6bb4f914739"
# Every constraint a migration after the baseline creates only when a check
# says its table lacks it (has_constraint, create_unique_constraint_if_missing
# or a reflected foreign-key list), so adoption must re-create each one.
_GUARDED_CONSTRAINTS = {
    # f186cf8aa5c4 media
    "uq_anime_episodes_season_id_episode_number": "anime_episodes",
    "uq_tv_episodes_season_id_episode_number": "tv_episodes",
    "users_oidc_subject_key": "users",
    # f1b4375a624c invitation recipients
    "uq_notification_pending_endpoint": "notification_destinations",
    "fk_notification_destinations_pending_recipient": "notification_destinations",
    "ck_notification_destination_recipient_owner": "notification_destinations",
    "uq_notifications_pending_dedupe": "notifications",
    "fk_notifications_pending_recipient": "notifications",
    "ck_notifications_recipient_owner": "notifications",
    # 626d06554967 personal plugin schedules
    "fk_job_settings_user_id": "job_settings",
}


def _url(schema: str | None = None) -> URL:
    url = make_url(settings.DATABASE_URL.replace("+asyncpg", "+psycopg"))
    return url.update_query_dict({"options": f"-csearch_path={schema}"}) if schema else url


def _constraints(conn: Connection, schema: str) -> set[tuple[str, str, str]]:
    """Every (table, constraint, type) in the schema."""
    rows = conn.execute(
        text(
            "SELECT c.relname, k.conname, k.contype FROM pg_constraint k "
            "JOIN pg_class c ON c.oid = k.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :schema"
        ),
        {"schema": schema},
    )
    return {(table, name, kind) for table, name, kind in rows}


def _owners(conn: Connection, names: list[str], schemas: list[str]) -> dict[str, list[tuple]]:
    """Which (schema, table) owns each named constraint, within these schemas."""
    rows = conn.execute(
        text(
            "SELECT k.conname, n.nspname, c.relname FROM pg_constraint k "
            "JOIN pg_class c ON c.oid = k.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE k.conname = ANY(:names) AND n.nspname = ANY(:schemas) "
            "ORDER BY k.conname, n.nspname"
        ),
        {"names": names, "schemas": schemas},
    )
    owners: dict[str, list[tuple]] = {name: [] for name in names}
    for name, schema, table in rows:
        owners[name].append((schema, table))
    return owners


def test_constraint_check_ignores_identical_names_on_other_relations():
    applied, isolated = (f"migration_helpers_{kind}_{uuid4().hex}" for kind in ("a", "b"))
    names = ["example_owner", "example_owner_fk", "example_label_check"]
    engine = create_engine(_url())
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                for schema in (applied, isolated):
                    conn.execute(text(f"CREATE SCHEMA {schema}"))
                    conn.execute(text(f"CREATE TABLE {schema}.owners (id integer PRIMARY KEY)"))
                    conn.execute(
                        text(
                            f"CREATE TABLE {schema}.examples "
                            "(id integer PRIMARY KEY, owner_id integer, label text)"
                        )
                    )
                # The applied schema has every constraint and index already.
                conn.execute(
                    text(
                        f"ALTER TABLE {applied}.examples "
                        "ADD CONSTRAINT example_owner UNIQUE (owner_id), "
                        "ADD CONSTRAINT example_owner_fk FOREIGN KEY (owner_id) "
                        f"REFERENCES {applied}.owners (id), "
                        "ADD CONSTRAINT example_label_check CHECK (label <> '')"
                    )
                )
                conn.execute(text(f"CREATE INDEX ix_examples_label ON {applied}.examples (label)"))
                # The isolated schema has one of the names, but on another table.
                conn.execute(
                    text(
                        f"CREATE TABLE {isolated}.others "
                        "(id integer, CONSTRAINT example_label_check CHECK (id > 0))"
                    )
                )

                conn.execute(text(f"SET LOCAL search_path TO {isolated}"))
                with Operations.context(MigrationContext.configure(conn)):
                    for name in names:
                        assert not h.has_constraint("examples", name)
                    assert h.has_constraint("others", "example_label_check")
                    assert not h.has_constraint("missing_table", "example_owner")
                    assert not h.has_index("examples", "ix_examples_label")
                    h.create_unique_constraint_if_missing("example_owner", "examples", ["owner_id"])
                    assert h.has_constraint("examples", "example_owner")
                    # Running it again (as an adopted database does) is a no-op.
                    h.create_unique_constraint_if_missing("example_owner", "examples", ["owner_id"])

                conn.execute(text(f"SET LOCAL search_path TO {applied}"))
                with Operations.context(MigrationContext.configure(conn)):
                    for name in names:
                        assert h.has_constraint("examples", name)
                    assert not h.has_constraint("others", "example_label_check")
                    h.create_unique_constraint_if_missing("example_owner", "examples", ["owner_id"])

                assert _owners(conn, names, [applied, isolated]) == {
                    "example_owner": [(applied, "examples"), (isolated, "examples")],
                    "example_owner_fk": [(applied, "examples")],
                    "example_label_check": [(applied, "examples"), (isolated, "others")],
                }
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def _run(schema: str, *arguments: str) -> str:
    url = _url(schema).render_as_string(hide_password=False)
    environment = {**os.environ, "DATABASE_URL": url}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-m", *arguments],
        cwd=_BACKEND,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _alembic(schema: str, *arguments: str) -> None:
    _run(schema, "alembic", "-c", "alembic.ini", *arguments)


def test_isolated_schema_upgrade_adoption_and_downgrade_use_their_own_constraints():
    applied, isolated = (f"migration_constraints_{kind}_{uuid4().hex}" for kind in ("a", "b"))
    schemas = [applied, isolated]
    names = list(_GUARDED_CONSTRAINTS)
    both = {
        name: [(applied, table), (isolated, table)] for name, table in _GUARDED_CONSTRAINTS.items()
    }
    engine = create_engine(_url(), isolation_level="AUTOCOMMIT")
    scoped = create_engine(_url(isolated))
    try:
        with engine.connect() as conn:
            for schema in schemas:
                conn.execute(text(f"CREATE SCHEMA {schema}"))
            try:
                _alembic(applied, "upgrade", "head")
                _alembic(isolated, "upgrade", "head")
                # The second schema gets every constraint the first one has.
                complete = _constraints(conn, applied)
                assert _constraints(conn, isolated) == complete
                assert _owners(conn, names, schemas) == both

                # A database from an older history may lack them and hold the
                # duplicates the unique constraint forbids. Adoption tidies and
                # constrains this schema although the other one has the names.
                for name, table in _GUARDED_CONSTRAINTS.items():
                    conn.execute(text(f"ALTER TABLE {isolated}.{table} DROP CONSTRAINT {name}"))
                user_id, show_id, season_id, dropped_id, kept_id = (uuid4() for _ in range(5))
                with scoped.begin() as seed:
                    seed.execute(
                        insert(User).values(
                            id=user_id,
                            username=user_id.hex,
                            email=f"{user_id}@example.test",
                            password_hash="fixture",
                        )
                    )
                    seed.execute(
                        insert(TVShow).values(
                            id=show_id, user_id=user_id, title="Adopted", sort_title="adopted"
                        )
                    )
                    seed.execute(
                        insert(TVSeason).values(id=season_id, show_id=show_id, season_number=1)
                    )
                    for episode_id, watched in ((dropped_id, False), (kept_id, True)):
                        seed.execute(
                            insert(TVEpisode).values(
                                id=episode_id,
                                season_id=season_id,
                                episode_number=1,
                                watched=watched,
                            )
                        )
                    seed.execute(text("DELETE FROM alembic_version"))
                assert "Database: adopt" in _run(isolated, "src.database.migrate")
                assert _constraints(conn, isolated) == complete
                assert _owners(conn, names, schemas) == both
                with scoped.connect() as check:
                    episodes = check.execute(
                        select(TVEpisode.id, TVEpisode.watched).where(
                            TVEpisode.season_id == season_id
                        )
                    ).all()
                assert episodes == [(kept_id, True)]

                # The downgrades drop each constraint unconditionally; they
                # succeed because the upgrades created them in this schema,
                # and leave the other schema's identically named ones alone.
                _alembic(isolated, "downgrade", _BASELINE)
                assert _owners(conn, names, schemas) == {
                    name: [(applied, table)] for name, table in _GUARDED_CONSTRAINTS.items()
                }
                assert _constraints(conn, applied) == complete
            finally:
                for schema in schemas:
                    conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    finally:
        scoped.dispose()
        engine.dispose()
