"""Deleting a game removes its links, and only its links, at the database level (issue #472)."""

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from src.core.config import settings
from src.database.models.game import Game, GameLink
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.trash import sweep
from src.helpers import save_game_asset
from src.main import app  # noqa: F401 — register the complete application model graph

PREVIOUS_REVISION = "9cc6333e6de6"
THIS_REVISION = "b7d3a9e14c52"
_DELETE_RULES = {
    "a": "NO ACTION",
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}


def _sync_url() -> str:
    return (
        make_url(settings.DATABASE_URL)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


def _game_link_delete_rule(connection: psycopg.Connection) -> str:
    rows = connection.execute(
        "SELECT confdeltype FROM pg_constraint WHERE contype = 'f' "
        "AND conrelid = to_regclass('game_links') AND confrelid = to_regclass('games')"
    ).fetchall()
    assert len(rows) == 1, "game_links must have exactly one foreign key to games"
    return _DELETE_RULES[rows[0][0]]


def test_game_links_foreign_key_cascades_in_the_migrated_database():
    with psycopg.connect(_sync_url()) as connection:
        assert _game_link_delete_rule(connection) == "CASCADE"


def test_the_model_declares_the_same_rule_as_the_migration():
    (foreign_key,) = GameLink.__table__.c.game_id.foreign_keys
    assert foreign_key.ondelete == "CASCADE"
    assert foreign_key.column.table.name == "games"


def test_no_foreign_key_into_games_or_users_is_left_without_a_delete_rule():
    """Deleting a game or an account must never stop on a child nobody gave a rule."""
    with psycopg.connect(_sync_url()) as connection:
        unruled = connection.execute(
            "SELECT conrelid::regclass::text, conname FROM pg_constraint "
            "WHERE contype = 'f' AND confdeltype IN ('a', 'r') "
            "AND confrelid IN (to_regclass('games'), to_regclass('users'))"
        ).fetchall()
    assert unruled == []


def _user(label: str) -> User:
    user_id = uuid4()
    return User(
        id=user_id,
        username=f"{label}-{user_id}",
        email=f"{user_id}@example.test",
        password_hash="unused",
    )


def _game(user: User, title: str, **extra) -> Game:
    return Game(
        id=uuid4(), user_id=user.id, title=title, sort_title=title, folder_location=title, **extra
    )


def _links(game: Game, *labels: str) -> list[GameLink]:
    return [
        GameLink(game_id=game.id, label=label, url=f"https://example.test/{label}")
        for label in labels
    ]


async def _link_labels(game_ids: list[UUID]) -> list[str]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(GameLink.label).where(GameLink.game_id.in_(game_ids)).order_by(GameLink.label)
        )
        return list(rows)


async def _count_links_of_missing_games() -> int:
    async with SessionLocal() as db:
        return await db.scalar(
            select(func.count())
            .select_from(GameLink)
            .outerjoin(Game, Game.id == GameLink.game_id)
            .where(Game.id.is_(None))
        )


async def _seed() -> tuple[list[User], dict[str, Game]]:
    owners = [_user("links-a"), _user("links-b")]
    games = {
        "doomed": _game(owners[0], "Doomed"),
        "sibling": _game(owners[0], "Sibling"),
        "other": _game(owners[1], "Other"),
    }
    async with SessionLocal() as db:
        db.add_all(owners)
        await db.flush()
        db.add_all(games.values())
        await db.flush()
        db.add_all(
            [
                *_links(games["doomed"], "d1", "d2"),
                *_links(games["sibling"], "s1"),
                *_links(games["other"], "o1", "o2"),
            ]
        )
        await db.commit()
    return owners, games


async def _cleanup(owners: list[User]) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_([owner.id for owner in owners])))
        await db.commit()


@pytest.mark.asyncio
async def test_deleting_an_account_with_linked_games_removes_only_its_own_links():
    owners, games = await _seed()
    try:
        async with SessionLocal() as db:
            await db.delete(await db.get(User, owners[0].id))
            await db.commit()
        everything = [game.id for game in games.values()]
        assert await _link_labels(everything) == ["o1", "o2"]
        async with SessionLocal() as db:
            assert await db.get(Game, games["other"].id) is not None
            assert await db.get(Game, games["doomed"].id) is None
            assert await db.get(Game, games["sibling"].id) is None
        assert await _count_links_of_missing_games() == 0
    finally:
        await _cleanup(owners)


@pytest.mark.asyncio
async def test_a_plain_database_delete_of_a_game_removes_its_links_and_no_others():
    """A bulk DELETE never loads the ORM relationship, so only the database rule can help."""
    owners, games = await _seed()
    try:
        async with SessionLocal() as db:
            await db.execute(delete(Game).where(Game.id == games["doomed"].id))
            await db.commit()
        everything = [game.id for game in games.values()]
        assert await _link_labels(everything) == ["o1", "o2", "s1"]
        assert await _count_links_of_missing_games() == 0
    finally:
        await _cleanup(owners)


@pytest.mark.asyncio
async def test_deleting_a_game_through_the_session_still_removes_its_links():
    owners, games = await _seed()
    try:
        async with SessionLocal() as db:
            await db.delete(await db.get(Game, games["doomed"].id))
            await db.commit()
        everything = [game.id for game in games.values()]
        assert await _link_labels(everything) == ["o1", "o2", "s1"]
    finally:
        await _cleanup(owners)


@pytest.mark.asyncio
async def test_removing_a_link_from_a_game_keeps_the_game_and_its_other_links():
    owners, games = await _seed()
    try:
        async with SessionLocal() as db:
            game = await db.get(Game, games["doomed"].id)
            game.links = [link for link in game.links if link.label == "d1"]
            await db.commit()
        assert await _link_labels([games["doomed"].id]) == ["d1"]
        async with SessionLocal() as db:
            assert await db.get(Game, games["doomed"].id) is not None
    finally:
        await _cleanup(owners)


@pytest.mark.asyncio
async def test_a_link_cannot_be_created_for_a_missing_game():
    """Foreign-key enforcement stays on: the cascade must not turn into silent orphans."""
    async with SessionLocal() as db:
        db.add(GameLink(game_id=uuid4(), label="orphan", url="https://example.test/orphan"))
        with pytest.raises(IntegrityError):
            await db.commit()


@pytest.mark.asyncio
async def test_a_trash_purge_removes_the_expired_games_links_and_keeps_recoverable_ones(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)
    cutoff = int(time.time()) - sweep.RETENTION_SECONDS
    owner = _user("purge")
    expired = _game(owner, "expired", deleted_at=cutoff - 60)
    recent = _game(owner, "recent", deleted_at=cutoff + 60)
    try:
        async with SessionLocal() as db:
            db.add(owner)
            await db.flush()
            db.add_all([expired, recent])
            await db.flush()
            db.add_all([*_links(expired, "e1"), *_links(recent, "r1")])
            await db.commit()
        async with SessionLocal() as db:
            # pylint: disable-next=protected-access
            assert await sweep._purge_game(db, expired.id, expired.id, cutoff)
            await db.commit()
        assert await _link_labels([expired.id, recent.id]) == ["r1"]
        async with SessionLocal() as db:
            assert await db.get(Game, recent.id) is not None
    finally:
        await _cleanup([owner])


def test_upgrade_and_downgrade_keep_existing_links_and_change_only_the_delete_rule():
    schema = "game_links_migration_" + uuid4().hex
    scoped = make_url(settings.DATABASE_URL).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
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

    def link_rows(connection):
        return connection.execute("SELECT label, url FROM game_links ORDER BY label").fetchall()

    with psycopg.connect(_sync_url(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", PREVIOUS_REVISION)
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            assert _game_link_delete_rule(connection) == "NO ACTION"
            user_id, kept, removed = uuid4(), uuid4(), uuid4()
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
                    for game_id in (kept, removed):
                        db.execute(
                            Game.__table__.insert().values(
                                id=game_id,
                                user_id=user_id,
                                title=str(game_id),
                                sort_title=str(game_id),
                                folder_location=str(game_id),
                            )
                        )
                    for game_id, label in ((kept, "keep-a"), (kept, "keep-b"), (removed, "gone")):
                        db.execute(
                            GameLink.__table__.insert().values(
                                game_id=game_id, label=label, url=f"https://example.test/{label}"
                            )
                        )
            finally:
                engine.dispose()
            before = link_rows(connection)
            assert [row[0] for row in before] == ["gone", "keep-a", "keep-b"]

            # Before the migration the database refuses to delete a game that still has links.
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                connection.execute("DELETE FROM games WHERE id = %s", (removed,))

            migrate("upgrade", THIS_REVISION)
            assert _game_link_delete_rule(connection) == "CASCADE"
            assert link_rows(connection) == before
            migrate("upgrade", THIS_REVISION)  # running it again changes nothing
            assert link_rows(connection) == before

            migrate("downgrade", PREVIOUS_REVISION)
            assert _game_link_delete_rule(connection) == "NO ACTION"
            assert link_rows(connection) == before
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                connection.execute("DELETE FROM games WHERE id = %s", (removed,))

            migrate("upgrade", THIS_REVISION)
            assert _game_link_delete_rule(connection) == "CASCADE"
            connection.execute("DELETE FROM games WHERE id = %s", (removed,))
            assert [row[0] for row in link_rows(connection)] == ["keep-a", "keep-b"]
            connection.execute("DELETE FROM users WHERE id = %s", (user_id,))
            assert link_rows(connection) == []
            assert connection.execute("SELECT count(*) FROM games").fetchone() == (0,)
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
