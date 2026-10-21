"""Achievement lists reported by metadata providers: contract, storage and matching."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Response
from pydantic import ValidationError
from sqlalchemy import delete, select

from src.api.routes import games, library_sync
from src.api.schemas.game import GameCreate
from src.database.models.achievement import Achievement
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import delegated_library
from src.features.achievement_catalogue import catalogue_rows, store_catalogue
from src.features.achievement_folding import HIDDEN_NAME
from src.plugin_api.metadata_contracts import MetadataPatch


async def store_catalog(db, game_id, items, provider="official.metadata-atlas"):
    """Store contract-shaped items the way a refresh does."""
    await store_catalogue(
        db, game_id, provider, catalogue_rows({"metadata": {"achievements": items}})
    )


def _item(external_id="a1", **extra):
    return {"external_id": external_id, "name": f"Name {external_id}", **extra}


def test_patch_accepts_a_bounded_achievement_list():
    patch = MetadataPatch(
        achievements=[
            _item(
                hidden=True,
                kind="missable",
                tier="gold",
                points=50,
                global_percent=1.5,
                icon_url="https://atlas.example/api/v2/media/abc",
                source_ids={"steam": "ACH_X"},
            )
        ]
    )
    assert patch.achievements[0].kind == "missable" and patch.achievements[0].points == 50
    assert MetadataPatch().achievements == ()


@pytest.mark.parametrize(
    "bad",
    [
        {"name": ""},
        {"name": "x" * 201},
        {"kind": "grind"},
        {"tier": "platinum"},
        {"points": -1},
        {"global_percent": 100.5},
        {"global_percent": float("nan")},
        {"icon_url": "javascript:alert(1)"},
        {"icon_url": "file:///etc/passwd"},
        {"source_ids": {"Bad Key": "x"}},
        {"source_ids": {f"k{i}": "x" for i in range(9)}},
        {"unknown": 1},
    ],
)
def test_patch_rejects_unsafe_or_oversized_achievements(bad):
    with pytest.raises(ValidationError):
        MetadataPatch(achievements=[{**_item(), **bad}])


def test_patch_caps_the_list_length():
    with pytest.raises(ValidationError):
        MetadataPatch(achievements=[_item(str(n)) for n in range(2001)])


@pytest.fixture(autouse=True)
def no_game_folders(monkeypatch):
    monkeypatch.setattr(games, "create_game_folder", lambda *_args: None)


@pytest.fixture
async def scratch_user():
    async with SessionLocal() as db:
        user = User(
            username=f"t_{uuid.uuid4().hex[:10]}",
            email=f"{uuid.uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.commit()
        user_id = user.id
    yield SimpleNamespace(id=user_id)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


async def _game(user, db):
    game = await games.create_game(
        GameCreate(title="Catalogue Test", folder_location=f"Cat_{uuid.uuid4().hex[:6]}"),
        Response(),
        db,
        user,
    )
    return game.id


def _synced(external_id, name, unlocked=False, hidden=False, icon=None):
    return {
        "external_id": external_id,
        "name": name,
        "description": None,
        "icon_url": icon,
        "unlocked": unlocked,
        "unlocked_at": 1_700_000_000 if unlocked else None,
        "hidden": hidden,
    }


async def test_catalogue_lends_details_to_the_matching_synced_row_without_duplicating(
    scratch_user,
):
    async with SessionLocal() as db:
        game_id = await _game(scratch_user, db)
        await library_sync._replace_achievements(  # pylint: disable=protected-access
            db, game_id, "Steam", [_synced("ACH_X", "Won", unlocked=True)]
        )
        await store_catalog(
            db,
            game_id,
            [
                {
                    **_item("atlas-1", name="Won (catalogue)"),
                    "description": "Win once.",
                    "icon_url": "https://atlas.example/m/1",
                    "locked_icon_url": "https://atlas.example/m/1g",
                    "points": 25,
                    "global_percent": 12.5,
                    "kind": "progression",
                    "source_ids": {"steam": "ACH_X"},
                },
                _item("atlas-2", name="Unplayed one"),
            ],
        )
        await db.commit()
        listed = await games.list_game_achievements(game_id, db, scratch_user)
    by_name = {item["name"]: item for item in listed}
    assert set(by_name) == {"Won", "Unplayed one"}  # the matched catalogue row is not a copy
    won = by_name["Won"]
    assert won["unlocked"] is True and won["provider"] == "Steam"
    assert won["description"] == "Win once." and won["points"] == 25
    assert won["global_percent"] == 12.5 and won["tier"] == "progression"
    assert won["icon_url"].startswith("/api/achievement-icon/")
    assert won["locked_icon_url"].endswith("/locked")
    assert by_name["Unplayed one"]["unlocked"] is False
    assert by_name["Unplayed one"]["provider"] == "official.metadata-atlas"


async def test_a_refresh_replaces_the_list_but_an_empty_answer_changes_nothing(scratch_user):
    async with SessionLocal() as db:
        game_id = await _game(scratch_user, db)
        await store_catalog(db, game_id, [_item("a"), _item("b")])
        await db.commit()
        await store_catalog(db, game_id, [])  # an empty answer changes nothing
        await db.commit()
        assert (
            len(list(await db.scalars(select(Achievement).where(Achievement.game_id == game_id))))
            == 2
        )
        await store_catalog(db, game_id, [_item("a"), _item("c")])  # b gone, c new
        await db.commit()
        names = set(
            await db.scalars(select(Achievement.external_id).where(Achievement.game_id == game_id))
        )
    assert names == {"a", "c"}


async def test_a_refresh_never_touches_synced_progress(scratch_user):
    async with SessionLocal() as db:
        game_id = await _game(scratch_user, db)
        await library_sync._replace_achievements(  # pylint: disable=protected-access
            db, game_id, "Steam", [_synced("ACH_X", "Mine", unlocked=True)]
        )
        await store_catalog(db, game_id, [_item("only-one")])
        await db.commit()
        row = await db.scalar(
            select(Achievement).where(
                Achievement.game_id == game_id, Achievement.provider == "Steam"
            )
        )
    assert row is not None and row.unlocked is True


async def test_summary_counts_a_catalogue_only_for_games_without_synced_rows(scratch_user):
    async with SessionLocal() as db:
        synced_id = await _game(scratch_user, db)
        catalogue_id = await _game(scratch_user, db)
        await library_sync._replace_achievements(  # pylint: disable=protected-access
            db, synced_id, "Steam", [_synced("A", "One", unlocked=True)]
        )
        await store_catalog(db, synced_id, [_item("x"), _item("y"), _item("z")])
        await store_catalog(db, catalogue_id, [_item("x"), _item("y")])
        await db.commit()
        summary = await games.get_achievements_summary(db, scratch_user)
    assert summary[str(synced_id)] == {"total": 1, "unlocked": 1}
    assert summary[str(catalogue_id)] == {"total": 2, "unlocked": 0}


async def test_shared_surface_hides_locked_hidden_achievements_and_the_catalogue(scratch_user):
    async with SessionLocal() as db:
        game_id = await _game(scratch_user, db)
        await library_sync._replace_achievements(  # pylint: disable=protected-access
            db,
            game_id,
            "Steam",
            [
                {**_synced("S1", "Secret boss", hidden=True), "description": "Kill the boss"},
                {**_synced("S2", "Secret earned", hidden=True, unlocked=True)},
            ],
        )
        await store_catalog(db, game_id, [_item("c1", name="Catalogue only")])
        await db.commit()
        page = await delegated_library.achievement_page(db, scratch_user.id, game_id, 0, 50)
    shown = {item["external_id"]: item for item in page["achievements"]}
    assert set(shown) == {"S1", "S2"}
    assert shown["S1"]["name"] == HIDDEN_NAME and shown["S1"]["description"] is None
    assert shown["S2"]["name"] == "Secret earned"
