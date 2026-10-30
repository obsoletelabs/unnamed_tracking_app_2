"""A preferred metadata source per game and field (#349)."""

# ruff: noqa: F811

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import insert, select

from src.api.routes import game_metadata
from src.core.auth import get_current_user
from src.database.models.game import Game
from src.database.models.game_identity import GameIdentity, GameUmbrella
from src.database.models.game_identity_tracking import FieldValueOrigin, GameFieldPreference
from src.database.session import SessionLocal
from src.features.game_identity import field_provenance
from src.main import app
from tests.test_game_ingestion import make_game, users  # noqa: F401


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


async def _record(game_id, values, provider):
    async with SessionLocal() as db:
        game = await db.get(Game, game_id)
        await field_provenance.record_field_sources(
            db, game, values, origin=FieldValueOrigin.METADATA, provider=provider
        )
        await db.commit()


async def _set(game_id, **values):
    async with SessionLocal() as db:
        game = await db.get(Game, game_id)
        for field, value in values.items():
            setattr(game, field, value)
        await db.commit()


async def _get(game_id):
    async with SessionLocal() as db:
        return await db.get(Game, game_id)


async def _group(user_id, *game_ids):
    umbrella_id = uuid4()
    async with SessionLocal() as db:
        await db.execute(
            insert(GameUmbrella).values(
                id=umbrella_id,
                user_id=user_id,
                title="Hades",
                sort_title="hades",
                created_at=1,
                updated_at=1,
            )
        )
        for position, game_id in enumerate(game_ids):
            await db.execute(
                insert(GameIdentity).values(
                    game_id=game_id,
                    user_id=user_id,
                    umbrella_id=umbrella_id,
                    is_primary=position == 0,
                    position=position,
                )
            )
        await db.commit()


async def _two_providers(user, **current):
    """A game whose developer was reported as 'A' by steam, then 'B' by igdb (current)."""
    game_id = await make_game(user)
    await _record(game_id, {"developer": "A"}, "steam")
    await _record(game_id, {"developer": "B"}, "igdb")
    await _set(game_id, developer="B", **current)
    return game_id


async def test_field_sources_lists_each_provider_value_with_the_current_one_marked(
    users,
    api,
):
    user = await users()
    game_id = await _two_providers(user)
    async with api(user) as client:
        seen = (await client.get(f"/api/game/{game_id}/field-sources")).json()
    sources = {row["source_key"]: row for row in seen["developer"]["sources"]}
    assert sources["provider:steam"]["value"] == "A" and not sources["provider:steam"]["current"]
    assert sources["provider:igdb"]["current"] is True
    assert seen["developer"]["preferred"] is None


async def test_preferring_a_provider_takes_its_value_and_keeps_provenance(users, api):
    user = await users()
    game_id = await _two_providers(user)
    async with api(user) as client:
        done = await client.put(
            f"/api/game/{game_id}/field-preferences/developer",
            json={"source_key": "provider:steam"},
        )
        assert done.status_code == 200, done.text
        assert done.json()["applied"] is True
        seen = (await client.get(f"/api/game/{game_id}/field-sources")).json()
    assert (await _get(game_id)).developer == "A"
    assert seen["developer"]["preferred"] == "provider:steam"
    assert seen["developer"]["provider"] == "steam"


async def test_a_lock_beats_a_preference(users, api):
    user = await users()
    game_id = await _two_providers(user)
    await _set(game_id, locked_fields=["developer"])
    async with api(user) as client:
        done = await client.put(
            f"/api/game/{game_id}/field-preferences/developer",
            json={"source_key": "provider:steam"},
        )
    assert done.json()["applied"] is False
    assert (await _get(game_id)).developer == "B"


async def test_a_copy_can_be_preferred_only_inside_its_umbrella(users, api):
    user = await users()
    main, copy, stranger = [await make_game(user, title=t) for t in ("Hades", "Hades 2", "Other")]
    await _set(copy, developer="Supergiant", tags=["roguelike"])
    await _set(stranger, developer="Nope")
    await _group(user, main, copy)
    url = f"/api/game/{main}/field-preferences/developer"
    async with api(user) as client:
        assert (await client.put(url, json={"source_key": f"copy:{stranger}"})).status_code == 422
        seen = (await client.get(f"/api/game/{main}/field-sources")).json()
        assert seen["developer"]["sources"][0]["source_key"] == f"copy:{copy}"
        done = await client.put(url, json={"source_key": f"copy:{copy}"})
    assert done.json()["applied"] is True
    assert (await _get(main)).developer == "Supergiant"


async def test_invalid_fields_and_sources_are_rejected_and_other_users_get_404(
    users,
    api,
):
    user, other = await users(), await users()
    game_id = await _two_providers(user)
    async with api(user) as client:
        for field, key in (
            ("title", "provider:igdb"),
            ("developer", "igdb"),
            ("developer", "copy:not-a-uuid"),
            ("developer", "provider:"),
        ):
            put = await client.put(
                f"/api/game/{game_id}/field-preferences/{field}", json={"source_key": key}
            )
            assert put.status_code == 422, (field, key)
        assert (
            await client.delete(f"/api/game/{game_id}/field-preferences/title")
        ).status_code == 422
    async with api(other) as client:
        put = await client.put(
            f"/api/game/{game_id}/field-preferences/developer",
            json={"source_key": "provider:igdb"},
        )
        assert put.status_code == 404
        assert (
            await client.delete(f"/api/game/{game_id}/field-preferences/developer")
        ).status_code == 404


async def test_clearing_a_preference_keeps_the_value(users, api):
    user = await users()
    game_id = await _two_providers(user)
    url = f"/api/game/{game_id}/field-preferences/developer"
    async with api(user) as client:
        await client.put(url, json={"source_key": "provider:steam"})
        assert (await client.delete(url)).status_code == 204
        assert (await client.delete(url)).status_code == 204
    assert (await _get(game_id)).developer == "A"
    async with SessionLocal() as db:
        left = await db.scalar(
            select(GameFieldPreference).where(GameFieldPreference.game_id == game_id)
        )
    assert left is None


def _refresh_with(monkeypatch, provider, **fields):
    async def resolve(*_args, **_kwargs):
        return {"providers": [provider], "contributors": []}, []

    monkeypatch.setattr(game_metadata, "resolve_library_record", resolve)
    monkeypatch.setattr(
        game_metadata,
        "game_result",
        lambda _record: {"title": "Hades", "provider": provider, **fields},
    )


async def _refresh(client, game_id):
    done = await client.post(
        f"/api/game/{game_id}/metadata/refresh", json={"fill_missing_art": False}
    )
    assert done.status_code == 200, done.text
    return done.json()


async def test_a_refresh_from_another_provider_leaves_a_preferred_field_alone(
    users,
    api,
    monkeypatch,
):
    user = await users()
    game_id = await _two_providers(user, publisher="Old")
    async with api(user) as client:
        await client.put(
            f"/api/game/{game_id}/field-preferences/developer",
            json={"source_key": "provider:steam"},
        )
        _refresh_with(monkeypatch, "igdb", developer="B2", publisher="New")
        done = await _refresh(client, game_id)
    game = await _get(game_id)
    assert game.developer == "A" and game.publisher == "New"
    assert done["changed_fields"] == ["publisher"]


async def test_a_refresh_from_the_preferred_provider_still_updates_the_field(
    users,
    api,
    monkeypatch,
):
    user = await users()
    game_id = await _two_providers(user)
    async with api(user) as client:
        await client.put(
            f"/api/game/{game_id}/field-preferences/developer",
            json={"source_key": "provider:steam"},
        )
        _refresh_with(monkeypatch, "steam", developer="A2")
        await _refresh(client, game_id)
    assert (await _get(game_id)).developer == "A2"


async def test_a_preferred_source_without_a_value_does_not_blank_or_block_the_field(
    users,
    api,
    monkeypatch,
):
    user = await users()
    game_id = await _two_providers(user)
    copy = await make_game(user, title="Hades copy")  # reports no developer
    await _group(user, game_id, copy)
    async with api(user) as client:
        put = await client.put(
            f"/api/game/{game_id}/field-preferences/developer",
            json={"source_key": f"copy:{copy}"},
        )
        assert put.json()["applied"] is False
        assert (await _get(game_id)).developer == "B"
        _refresh_with(monkeypatch, "igdb", developer="B2")
        await _refresh(client, game_id)
    assert (await _get(game_id)).developer == "B2"


async def test_a_lock_still_beats_a_preference_on_refresh(users, api, monkeypatch):
    user = await users()
    game_id = await _two_providers(user)
    async with api(user) as client:
        await client.put(
            f"/api/game/{game_id}/field-preferences/developer",
            json={"source_key": "provider:igdb"},
        )
        await _set(game_id, locked_fields=["developer"])
        _refresh_with(monkeypatch, "igdb", developer="B2")
        done = await _refresh(client, game_id)
    assert (await _get(game_id)).developer == "B"
    assert done["skipped_locked_fields"] == ["developer"]
