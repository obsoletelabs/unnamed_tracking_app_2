"""Who supplied a game's metadata values (#348)."""

from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.core.auth import get_current_user
from src.database.models.game import Game
from src.database.models.game_identity_tracking import (
    FieldValueOrigin,
    FieldValueState,
    GameFieldValue,
)
from src.database.session import SessionLocal
from src.features.game_identity import field_provenance
from src.main import app
from tests.test_game_ingestion import make_game, users  # noqa: F401


async def _record(game_id, values, **kw):
    async with SessionLocal() as db:
        game = await db.get(Game, game_id)
        await field_provenance.record_field_sources(db, game, values, **kw)
        await db.commit()


async def _sources(user_id, game_id):
    async with SessionLocal() as db:
        return await field_provenance.field_sources(db, user_id, game_id)


async def test_a_refresh_records_the_provider_and_a_later_edit_replaces_it(users):  # noqa: F811
    user = await users()
    game_id = await make_game(user)
    await _record(
        game_id,
        {"developer": "Supergiant", "release_date": date(2020, 9, 17), "tags": ["b", "a"]},
        origin=FieldValueOrigin.METADATA,
        provider="igdb",
    )
    seen = await _sources(user, game_id)
    assert seen["developer"]["origin"] == "metadata" and seen["developer"]["provider"] == "igdb"
    assert set(seen) == {"developer", "release_date", "tags"}

    await _record(game_id, {"developer": "Me"}, origin=FieldValueOrigin.USER, provider="ignored")
    seen = await _sources(user, game_id)
    assert seen["developer"]["origin"] == "user" and seen["developer"]["provider"] is None
    assert seen["tags"]["provider"] == "igdb"
    async with SessionLocal() as db:
        history = (
            await db.scalars(
                select(GameFieldValue).where(
                    GameFieldValue.game_id == game_id, GameFieldValue.field == "developer"
                )
            )
        ).all()
    assert sorted(row.state for row in history) == [
        FieldValueState.CURRENT.value,
        FieldValueState.SUPERSEDED.value,
    ]


async def test_the_same_value_from_the_same_source_is_not_recorded_twice(users):  # noqa: F811
    user = await users()
    game_id = await make_game(user)
    for _ in range(3):
        await _record(
            game_id, {"series": "Hades"}, origin=FieldValueOrigin.METADATA, provider="igdb"
        )
    async with SessionLocal() as db:
        rows = (
            await db.scalars(select(GameFieldValue).where(GameFieldValue.game_id == game_id))
        ).all()
    assert len(rows) == 1


async def test_fields_outside_the_metadata_set_are_ignored(users):  # noqa: F811
    user = await users()
    game_id = await make_game(user)
    await _record(game_id, {"title": "X", "status": "Playing"}, origin=FieldValueOrigin.USER)
    assert await _sources(user, game_id) == {}


async def test_a_metadata_value_without_a_provider_is_recorded_as_system(users):  # noqa: F811
    user = await users()
    game_id = await make_game(user)
    await _record(game_id, {"series": "S"}, origin=FieldValueOrigin.METADATA)
    assert (await _sources(user, game_id))["series"]["origin"] == "system"


async def test_database_rules_for_the_provider_label(users):  # noqa: F811
    user = await users()
    game_id = await make_game(user)

    def row(**kw):
        return GameFieldValue(user_id=user, game_id=game_id, field="series", value="S", **kw)

    async with SessionLocal() as db:
        db.add(row(origin="metadata", source_label="igdb"))
        await db.commit()
    for bad in (
        {"origin": "metadata"},
        {"origin": "user", "source_label": "igdb"},
        {"origin": "source"},
    ):
        async with SessionLocal() as db:
            db.add(row(state="superseded", **bad))
            with pytest.raises(IntegrityError):
                await db.commit()


def test_changed_values_keeps_only_differing_metadata_fields():
    game = Game(developer="A", publisher="B", title="T")
    assert field_provenance.changed_values(
        game, {"developer": "A", "publisher": "C", "title": "U"}
    ) == {"publisher": "C"}


@pytest.fixture
async def api():
    holder = {}

    def make(user_id):
        holder["id"] = user_id
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=holder["id"])
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    yield make
    app.dependency_overrides.pop(get_current_user, None)


async def test_a_manual_edit_is_listed_by_the_endpoint_and_only_for_the_owner(
    users,  # noqa: F811
    api,
):
    user, other = await users(), await users()
    game_id = await make_game(user)
    async with api(user) as client:
        edit = await client.patch(
            f"/api/game/update/{game_id}", json={"developer": "Me", "publisher": None}
        )
        assert edit.status_code == 200, edit.text
        seen = (await client.get(f"/api/game/{game_id}/field-sources")).json()
        assert seen["developer"]["origin"] == "user"
        assert "title" not in seen
    async with api(other) as client:
        assert (await client.get(f"/api/game/{game_id}/field-sources")).status_code == 404
