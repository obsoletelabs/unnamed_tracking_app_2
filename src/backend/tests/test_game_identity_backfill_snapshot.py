"""The backfill on a realistic populated database snapshot (#102).

A schema is migrated to the revision before the identity model, filled with a
mixed library the way an installation looked then, upgraded to head and
backfilled twice through the same code the operator command runs."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.config import settings
from src.database.models.game_identity import GameIdentity, GameSourceObservation
from src.features.game_identity_backfill import progress, runner, stages

BASELINE = "c10098e58426"
COLUMNS = (
    "id, user_id, folder_location, title, sort_title, source, external_id, playnite_guid,"
    " parent_game_id, relationship_type, playtime_seconds, last_played_at, status, platform,"
    " notes, collections, favorite, deleted_at"
)


def library(user_id: UUID, other_id: UUID):
    """(id, user, title, source, external, guid, parent, type, played, last, status, platform,
    notes, collections, favorite, deleted_at) rows plus dismissed pairs and achievements."""
    rows, parents = [], {}
    for n in range(300):
        game_id, kind = uuid4(), n % 6
        base = [game_id, user_id, f"Game {n % 140}", None, None, None, None, None, 0, None]
        status, platform, notes = "BACKLOG", "PC", None
        if kind == 0:  # Steam, owned
            base[3:5] = ["Steam", str(1000 + n)]
            base[8:10] = [3600 * n, 1_700_000_000 + n]
            status = "PLAYED"
        elif kind == 1:  # manual
            base[3] = "physical"
            status = "BEATEN" if n % 4 == 1 else "PLAYING"
            notes = f"note {n}"
        elif kind == 2:  # Playnite
            base[5] = uuid4()
            base[8] = 100 * n
        elif kind == 3:  # plugin
            base[3:5] = ["Epic Games", f"epic-{n}"]
        elif kind == 4:  # wishlist placeholder
            base[2] = f"Steam app {5000 + n}"
            base[3:5] = ["Steam", str(5000 + n)]
            status = "WISHLIST"
        else:  # manual, trashed
            base[3] = "GOG"
        deleted = 1_700_100_000 if n % 29 == 0 else None
        rows.append(
            (
                *base,
                status,
                platform,
                notes,
                ["Backlog"] if n % 7 == 0 else [],
                n % 11 == 0,
                deleted,
            )
        )
        parents[n] = game_id
    for n in range(20):  # owned copies
        main, copy = parents[kind_index(n, 0)], uuid4()
        rows.append(
            (
                copy,
                user_id,
                "Copy",
                "physical",
                None,
                None,
                main,
                "owned_copy",
                60,
                None,
                "PLAYING",
                "PlayStation 5",
                None,
                [],
                False,
                None,
            )
        )
    for n in range(8):  # DLC
        rows.append(
            (
                uuid4(),
                user_id,
                f"DLC {n}",
                "Steam",
                str(9000 + n),
                None,
                parents[kind_index(n, 0)],
                "dlc",
                0,
                None,
                "BACKLOG",
                "PC",
                None,
                [],
                False,
                None,
            )
        )
    for n in range(5):  # one Steam record on two rows
        rows.append(
            (
                uuid4(),
                user_id,
                "Shared",
                "Steam",
                "777",
                None,
                None,
                None,
                n,
                None,
                "PLAYED",
                "PC",
                None,
                [],
                False,
                None,
            )
        )
    ids = [parents[kind_index(n, 1)] for n in range(12)]
    dismissed = [tuple(sorted(ids[i : i + 2])) for i in range(0, 12, 2)]
    rows.append(
        (
            uuid4(),
            other_id,
            "Game 1",
            "Steam",
            "1000",
            None,
            None,
            None,
            5,
            None,
            "PLAYED",
            "PC",
            None,
            [],
            False,
            None,
        )
    )
    return rows, dismissed, [parents[kind_index(n, 0)] for n in range(10)]


def kind_index(n: int, kind: int) -> int:
    return n * 6 + kind


@pytest.fixture
def snapshot_schema():
    schema = "backfill_snapshot_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
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
            timeout=180,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        yield schema, scoped, migrate, connection
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def test_backfill_a_realistic_snapshot_twice_after_a_real_upgrade(
    snapshot_schema, monkeypatch
):
    schema, scoped, migrate, connection = snapshot_schema
    migrate("upgrade", BASELINE)
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    user_id, other_id = uuid4(), uuid4()
    for uid in (user_id, other_id):
        connection.execute(
            "INSERT INTO users (id, username, email, password_hash, is_active, is_admin,"
            " created_at, updated_at) VALUES (%s, %s, %s, 'x', true, false, 1, 1)",
            (uid, uid.hex, f"{uid}@example.test"),
        )
    rows, dismissed, achievement_games = library(user_id, other_id)
    for (
        gid,
        uid,
        title,
        source,
        external,
        guid,
        parent,
        kind,
        played,
        last,
        status,
        platform,
        notes,
        collections,
        favorite,
        deleted,
    ) in rows:
        connection.execute(
            f"INSERT INTO games ({COLUMNS}, locked_fields, provider_ids, tags, features,"
            " profiles_enabled, osrs_stats_enabled, created_at, updated_at) VALUES (%s, %s, %s, %s,"
            " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, ARRAY[]::varchar[],"
            " '{}', '{}', '{}', false, false, 1, 1)",
            (
                gid,
                uid,
                gid.hex,
                title,
                title.lower(),
                source,
                external,
                guid,
                parent,
                kind,
                played,
                last,
                status,
                platform,
                notes,
                collections,
                favorite,
                deleted,
            ),
        )
    for first, second in dismissed:
        connection.execute(
            "INSERT INTO game_duplicate_dismissals (user_id, first_game_id, second_game_id,"
            " created_at) VALUES (%s, %s, %s, 1)",
            (user_id, first, second),
        )
    for game_id in achievement_games:
        for n in range(5):
            connection.execute(
                "INSERT INTO achievements (id, game_id, provider, external_id, name, unlocked,"
                " hidden, created_at) VALUES (%s, %s, 'Steam', %s, %s, %s, false, 1)",
                (uuid4(), game_id, f"a{n}", f"A{n}", n < 2),
            )
    dump = "SELECT * FROM games ORDER BY id"
    before = connection.execute(dump).fetchall()
    migrate("upgrade", "head")
    assert connection.execute(dump).fetchall() == before  # the schema upgrade touched no game

    engine = create_async_engine(scoped.render_as_string(hide_password=False))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    for module in (stages, progress, runner):
        monkeypatch.setattr(module, "SessionLocal", factory)
    try:
        first = await runner.run_user(user_id, batch_size=64)
        assert first["status"] == "completed", first["errors"]
        assert first["errors"] == [] or {e["code"] for e in first["errors"]} <= {
            "owned_copy_kept_separate"
        }
        games = first["before"]["games"]
        assert games == len(rows) - 1
        tables = (
            "game_umbrellas",
            "game_identities",
            "game_source_observations",
            "game_source_observation_links",
            "game_identity_decisions",
            "game_relationships",
            "game_metric_counters",
            "game_field_values",
        )
        snapshot = {t: connection.execute(f"SELECT count(*) FROM {t}").fetchone() for t in tables}
        async with factory() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(GameIdentity)
                    .where(GameIdentity.user_id == user_id)
                )
                == games
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(GameSourceObservation)
                    .where(GameSourceObservation.user_id == user_id)
                )
                >= games
            )
            unplaced = await db.scalar(
                text(
                    "SELECT count(*) FROM games g WHERE g.user_id = :u AND NOT EXISTS "
                    "(SELECT 1 FROM game_identities i WHERE i.game_id = g.id)"
                ),
                {"u": user_id},
            )
        assert unplaced == 0
        second = await runner.run_user(user_id, batch_size=64)
        assert second["status"] == "completed"
        again = {t: connection.execute(f"SELECT count(*) FROM {t}").fetchone() for t in tables}
        assert again == snapshot  # the second run added nothing anywhere
        for name in (
            "observations_created",
            "links_created",
            "identities_created",
            "umbrellas_created",
            "relationships_created",
            "dismissals_migrated",
        ):
            assert not second["counts"].get(name)
        # every old column is still what it was (only locked_fields may grow)
        after = connection.execute(dump).fetchall()
        assert len(after) == len(before)
        names = [d.name for d in connection.execute(dump).description]
        keep = [i for i, n in enumerate(names) if n not in ("locked_fields", "updated_at")]
        assert [[r[i] for i in keep] for r in after] == [[r[i] for i in keep] for r in before]
        # the other user's library is untouched
        assert connection.execute(
            "SELECT count(*) FROM game_identities WHERE user_id = %s", (other_id,)
        ).fetchone() == (0,)
    finally:
        await engine.dispose()
