"""One game refreshed from four providers at once (Steam, IGDB, RAWG and Atlas).

The metadata handler merges every provider's patch into one record: each field comes from the
highest-priority provider that has it. These tests drive the real refresh route and handler
with stand-in providers and read the result back from PostgreSQL. They check that the merged
record is one game, that every field's recorded source is the provider that supplied it, that
a person's locks, preferences and play history survive, and that a second refresh (or a
library re-import) changes nothing."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

from sqlalchemy import func, select

from src.api.routes import library_sync
from src.database.models.game import Game, GameStatus
from src.database.models.game_identity_tracking import (
    FieldValueOrigin,
    FieldValueState,
    GameFieldPreference,
    GameFieldValue,
)
from src.database.session import SessionLocal
from src.features.game_identity import decisions, field_provenance
from src.features.game_identity.field_provenance import PROVENANCE_FIELDS
from src.features.imports.library_games import get_or_create_game
from src.plugin_api.metadata_contracts import MetadataPatch, MetadataRelease
from tests.test_game_metadata_refresh_identity import (
    ATLAS_ID,
    StandInProvider,
    add_game,
    refresh,
    stored,
)
from tests.test_game_metadata_refresh_identity import client as client  # noqa: F401 - fixture
from tests.test_game_metadata_refresh_identity import owner as owner  # noqa: F401 - fixture
from tests.test_game_metadata_refresh_identity import providers as providers  # noqa: F401

STEAM_ID = "620"
IGDB_ID = "72"
RAWG_ID = "4200"
ATLAS = "official.metadata-atlas.atlas"
RAWG = "community.metadata-rawg.rawg"


def _linked(provider_id: str, namespace: str, priority: int, **fields) -> StandInProvider:
    """A provider that answers for the game's Steam id and reports its own id back."""

    def respond(candidate):
        if candidate is None or candidate.provider_ids.get("steam") != STEAM_ID:
            return None
        return MetadataPatch(**fields)

    return StandInProvider(provider_id, namespace, respond, priority=priority)


def four_providers(**overrides: dict) -> tuple[StandInProvider, ...]:
    """Steam (first), IGDB, RAWG and Atlas, each holding a different part of the record."""
    fields = {
        "steam": {"title": "Portal 2", "description": "A puzzle game from Steam."},
        "igdb": {"developer": "Valve", "publisher": "Electronic Arts", "tags": ["Puzzle"]},
        "rawg": {"age_rating": "PEGI 12", "series": "Portal", "developer": "Valve (RAWG)"},
        "atlas": {
            "first_release": MetadataRelease(date="2011-04", precision="month"),
            "releases": (MetadataRelease(date="2011-04", precision="month", region="eu"),),
            "description": "An Atlas description that Steam outranks.",
        },
    }
    for name, changed in overrides.items():
        fields[name] = {**fields[name], **changed}
    return (
        _linked("core.steam", "steam", 0, provider_ids={"steam": STEAM_ID}, **fields["steam"]),
        _linked("core.igdb", "igdb", 20, provider_ids={"igdb": IGDB_ID}, **fields["igdb"]),
        _linked(RAWG, "rawg", 30, provider_ids={"rawg": RAWG_ID}, **fields["rawg"]),
        _linked(ATLAS, "atlas", 40, provider_ids={"atlas": ATLAS_ID}, **fields["atlas"]),
    )


async def current_sources(game_id) -> dict[str, tuple[str | None, object]]:
    """field -> (recorded provider, recorded value) of the current provenance rows (the title
    keeps its own rules in `names`)."""
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(GameFieldValue).where(
                GameFieldValue.game_id == game_id,
                GameFieldValue.state == FieldValueState.CURRENT.value,
                GameFieldValue.field.in_(PROVENANCE_FIELDS),
            )
        )
        return {row.field: (row.source_label, row.value) for row in rows}


async def games_of(user_id) -> int:
    async with SessionLocal() as db:
        return int(await db.scalar(select(func.count()).where(Game.user_id == user_id)) or 0)


async def test_four_providers_make_one_record_with_every_field_attributed_to_its_provider(
    owner, providers, client
):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})

    result = await refresh(client, game_id)

    assert result["status"] == "updated"
    game = await stored(game_id)
    assert game.provider_ids == {
        "steam": STEAM_ID,
        "igdb": IGDB_ID,
        "rawg": RAWG_ID,
        "atlas": ATLAS_ID,
    }
    # each field from the highest-priority provider that has it
    assert (game.title, game.description) == ("Portal 2", "A puzzle game from Steam.")
    assert (game.developer, game.publisher, game.tags) == ("Valve", "Electronic Arts", ["Puzzle"])
    assert (game.age_rating, game.series) == ("PEGI 12", "Portal")
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 1), "month")
    assert await games_of(user_id) == 1

    sources = await current_sources(game_id)
    assert sources == {
        "description": ("core.steam", "A puzzle game from Steam."),
        "developer": ("core.igdb", "Valve"),
        "publisher": ("core.igdb", "Electronic Arts"),
        "tags": ("core.igdb", ["Puzzle"]),
        "age_rating": (RAWG, "PEGI 12"),
        "series": (RAWG, "Portal"),
        # a month is recorded as the month, so preferring Atlas later restores the precision
        "release_date": (ATLAS, "2011-04"),
    }
    read = (await client.get(f"/api/game/{game_id}/field-sources")).json()
    assert read["developer"]["provider"] == "core.igdb"
    assert read["release_date"]["provider"] == ATLAS


async def test_a_day_from_one_provider_inside_another_providers_month_names_the_day_source(
    owner, providers, client
):
    user_id, _ = owner
    # Steam knows the day, Atlas only the month: the day wins (C5.2) and it is Steam's
    providers(*four_providers(steam={"release_date": date(2011, 4, 19)}))
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})

    await refresh(client, game_id)

    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 19), None)
    assert (await current_sources(game_id))["release_date"] == ("core.steam", "2011-04-19")


async def test_a_preferred_provider_keeps_its_field_when_a_higher_priority_one_disagrees(
    owner, providers, client
):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    await refresh(client, game_id)

    # the person prefers IGDB's developer; IGDB's value must be known by its own name
    response = await client.put(
        f"/api/game/{game_id}/field-preferences/developer",
        json={"source_key": "provider:core.igdb"},
    )
    assert response.status_code == 200, response.text

    # IGDB now names someone else; the preference follows IGDB, not the merged record's lead
    providers(*four_providers(igdb={"developer": "Valve Corporation"}))
    result = await refresh(client, game_id)

    assert result["status"] == "updated"
    game = await stored(game_id)
    assert game.developer == "Valve Corporation"
    assert (await current_sources(game_id))["developer"] == ("core.igdb", "Valve Corporation")


async def test_a_preference_for_a_lower_priority_provider_wins_over_the_merged_value(
    owner, providers, client
):
    user_id, _ = owner
    # RAWG's developer loses to IGDB's in the merge; the person prefers RAWG's
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    async with SessionLocal() as db:
        db.add(
            GameFieldPreference(
                user_id=user_id,
                game_id=game_id,
                field="developer",
                source_key=f"provider:{RAWG}",
                created_at=1,
            )
        )
        await db.commit()

    result = await refresh(client, game_id)

    # RAWG reported a developer this time, so the preference takes it although IGDB outranks it
    # (D2: before, only merge winners were known and the field kept IGDB's value for good)
    assert "developer" in result["changed_fields"]
    assert (await stored(game_id)).developer == "Valve (RAWG)"
    assert (await current_sources(game_id))["developer"] == (RAWG, "Valve (RAWG)")
    # IGDB's value, which won the merge, is kept as a candidate to switch back to
    read = (await client.get(f"/api/game/{game_id}/field-sources")).json()["developer"]
    assert {s["source_key"]: (s["value"], s["current"]) for s in read["sources"]} == {
        f"provider:{RAWG}": ("Valve (RAWG)", True),
        "provider:core.igdb": ("Valve", False),
    }
    # a second refresh with the same answers changes nothing
    again = await refresh(client, game_id)
    assert again["changed_fields"] == []
    assert (await stored(game_id)).developer == "Valve (RAWG)"


async def test_locks_and_play_history_survive_a_four_provider_refresh(owner, providers, client):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(
        user_id,
        provider_ids={"steam": STEAM_ID},
        developer="My own developer",
        release_date=date(2011, 4, 18),
        locked_fields=["developer", "release_date", "releases"],
        status=GameStatus.PLAYING,
        playtime_seconds=7200,
        last_played_at=1_700_000_000,
        rating_overall=9,
    )

    result = await refresh(client, game_id)

    assert result["status"] == "updated"
    assert {"developer", "release_date", "releases"} <= set(result["skipped_locked_fields"])
    game = await stored(game_id)
    assert game.developer == "My own developer"
    assert (game.release_date, game.release_date_precision, game.releases) == (
        date(2011, 4, 18),
        None,
        None,
    )
    assert game.locked_fields == ["developer", "release_date", "releases"]
    assert (game.status, game.playtime_seconds, game.last_played_at) == (
        GameStatus.PLAYING,
        7200,
        1_700_000_000,
    )
    assert float(game.rating_overall) == 9
    # unlocked fields still arrive
    assert (game.publisher, game.series) == ("Electronic Arts", "Portal")


async def test_a_second_refresh_with_the_same_answers_changes_nothing(owner, providers, client):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    await refresh(client, game_id)
    before = await stored(game_id)
    sources = await current_sources(game_id)

    again = await refresh(client, game_id)

    assert again["status"] == "updated"
    assert again["changed_fields"] == []
    after = await stored(game_id)
    assert after.updated_at == before.updated_at
    assert await current_sources(game_id) == sources
    async with SessionLocal() as db:
        rows = await db.scalar(
            select(func.count()).where(
                GameFieldValue.game_id == game_id,
                GameFieldValue.field.in_(PROVENANCE_FIELDS),
                GameFieldValue.state != FieldValueState.CANDIDATE.value,
            )
        )
        candidates = {
            (row.field, row.source_label): row.value
            for row in await db.scalars(
                select(GameFieldValue).where(
                    GameFieldValue.game_id == game_id,
                    GameFieldValue.state == FieldValueState.CANDIDATE.value,
                )
            )
        }
    assert rows == len(sources)
    # the values that lost the merge, one row each, not one more per refresh
    assert candidates == {
        ("developer", RAWG): "Valve (RAWG)",
        ("description", ATLAS): "An Atlas description that Steam outranks.",
    }


async def test_a_steam_reimport_after_merge_and_unmerge_reuses_both_games(owner, providers, client):
    user_id, _ = owner
    providers(*four_providers())
    steam_game = await add_game(
        user_id,
        title="Portal 2",
        source="steam",
        external_id=STEAM_ID,
        provider_ids={"steam": STEAM_ID},
        playtime_seconds=600,
    )
    other_copy = await add_game(user_id, title="Portal 2", source="manual", playtime_seconds=60)
    await refresh(client, steam_game)

    async with SessionLocal() as db:
        await decisions.merge_identities(db, user_id, other_copy, steam_game)
        await db.commit()
    async with SessionLocal() as db:
        game, created = await get_or_create_game(db, user_id, "Portal 2", "steam", STEAM_ID)
        await db.commit()
        assert (game.id, created) == (steam_game, False)
    async with SessionLocal() as db:
        await decisions.unmerge_identity(db, user_id, other_copy)
        await db.commit()
    async with SessionLocal() as db:
        game, created = await get_or_create_game(db, user_id, "Portal 2", "steam", STEAM_ID)
        await db.commit()
        assert (game.id, created) == (steam_game, False)

    assert await games_of(user_id) == 2
    merged = await stored(steam_game)
    assert merged.playtime_seconds == 600
    assert merged.provider_ids["igdb"] == IGDB_ID
    assert (await stored(other_copy)).playtime_seconds == 60


# --- data recorded before per-field sources: every field under "Library" -----------------------

# the fields a four-provider refresh stores, as an install refreshed before #348 holds them
REFRESHED = {
    "title": "Portal 2",
    "description": "A puzzle game from Steam.",
    "developer": "Valve",
    "publisher": "Electronic Arts",
    "tags": ["Puzzle"],
    "age_rating": "PEGI 12",
    "series": "Portal",
    "release_date": date(2011, 4, 1),
    "release_date_precision": "month",
    "releases": [
        MetadataRelease(date="2011-04", precision="month", region="eu").model_dump(mode="json")
    ],
}


async def legacy_rows(user_id, game_id, values: dict, *, user_fields=()) -> None:
    """The provenance an older refresh wrote: each value as current, labelled "Library"
    (the fields in `user_fields` as a person's own edit)."""
    async with SessionLocal() as db:
        for field, value in values.items():
            typed = field in user_fields
            db.add(
                GameFieldValue(
                    user_id=user_id,
                    game_id=game_id,
                    field=field,
                    value=value,
                    state=FieldValueState.CURRENT.value,
                    origin=(FieldValueOrigin.USER if typed else FieldValueOrigin.METADATA).value,
                    source_label=None if typed else "Library",
                    observed_at=None if typed else 1,
                    edited_at=1 if typed else None,
                    recorded_at=1,
                    locked=False,
                )
            )
        await db.commit()


async def provenance_rows(game_id) -> int:
    async with SessionLocal() as db:
        return int(
            await db.scalar(select(func.count()).where(GameFieldValue.game_id == game_id)) or 0
        )


async def test_an_unchanged_value_recorded_under_library_is_relabelled_by_the_next_refresh(
    owner, providers, client
):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID}, **REFRESHED)
    await legacy_rows(
        user_id,
        game_id,
        {
            "description": "A puzzle game from Steam.",
            "developer": "Valve",
            "publisher": "Electronic Arts",
            "tags": ["Puzzle"],
            "age_rating": "PEGI 12",
            "series": "Portal",
            "release_date": "2011-04-01",
        },
        user_fields=("series",),
    )

    result = await refresh(client, game_id)

    # nothing changed on the game itself
    assert result["status"] == "updated"
    assert result["changed_fields"] == []
    # but every value a provider supplied now names that provider; the person's own stays theirs
    assert await current_sources(game_id) == {
        "description": ("core.steam", "A puzzle game from Steam."),
        "developer": ("core.igdb", "Valve"),
        "publisher": ("core.igdb", "Electronic Arts"),
        "tags": ("core.igdb", ["Puzzle"]),
        "age_rating": (RAWG, "PEGI 12"),
        "series": (None, "Portal"),
        "release_date": (ATLAS, "2011-04"),
    }
    async with SessionLocal() as db:
        superseded = set(
            await db.scalars(
                select(GameFieldValue.field).where(
                    GameFieldValue.game_id == game_id,
                    GameFieldValue.source_label == "Library",
                    GameFieldValue.state == FieldValueState.SUPERSEDED.value,
                )
            )
        )
    assert superseded == {
        "description",
        "developer",
        "publisher",
        "tags",
        "age_rating",
        "release_date",
    }
    # the Library entries are no longer offered next to the real providers
    read = (await client.get(f"/api/game/{game_id}/field-sources")).json()
    assert read["developer"]["provider"] == "core.igdb"
    assert "provider:Library" not in {s["source_key"] for s in read["developer"]["sources"]}

    # and the relabelling happens once: a further refresh writes nothing
    before, count = await current_sources(game_id), await provenance_rows(game_id)
    await refresh(client, game_id)
    assert await current_sources(game_id) == before
    assert await provenance_rows(game_id) == count


async def test_a_library_preference_from_before_per_field_sources_still_follows_the_refresh(
    owner, providers, client
):
    user_id, _ = owner
    providers(*four_providers(igdb={"developer": "Valve Corporation"}))
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID}, **REFRESHED)
    await legacy_rows(user_id, game_id, {"developer": "Valve"})
    # "Prefer this source" could only ever name "Library", the refresh's own merged result
    async with SessionLocal() as db:
        db.add(
            GameFieldPreference(
                user_id=user_id,
                game_id=game_id,
                field="developer",
                source_key="provider:Library",
                created_at=1,
            )
        )
        await db.commit()

    result = await refresh(client, game_id)

    assert "developer" in result["changed_fields"]
    assert (await stored(game_id)).developer == "Valve Corporation"
    assert (await current_sources(game_id))["developer"] == ("core.igdb", "Valve Corporation")
    read = (await client.get(f"/api/game/{game_id}/field-sources")).json()
    assert read["developer"]["preferred"] == "provider:Library"
    # the legacy label is no longer offered; IGDB's value and RAWG's losing one are
    assert [s["source_key"] for s in read["developer"]["sources"]] == [
        f"provider:{RAWG}",
        "provider:core.igdb",
    ]


async def test_preferring_a_providers_month_restores_the_month_not_its_first_day(
    owner, providers, client
):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    await refresh(client, game_id)
    # Steam later reports the day, which wins over Atlas's month (C5.2)
    providers(*four_providers(steam={"release_date": date(2011, 4, 19)}))
    await refresh(client, game_id)
    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 19), None)

    response = await client.put(
        f"/api/game/{game_id}/field-preferences/release_date",
        json={"source_key": f"provider:{ATLAS}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True
    game = await stored(game_id)
    # April 2011, never 1 April 2011 (C5.5)
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 1), "month")
    assert (await current_sources(game_id))["release_date"] == (ATLAS, "2011-04")
    # asking again changes nothing
    again = await client.put(
        f"/api/game/{game_id}/field-preferences/release_date",
        json={"source_key": f"provider:{ATLAS}"},
    )
    assert again.json()["applied"] is False


class _NamedRawg(StandInProvider):
    """A community provider whose display name is "RAWG", as two plugins can both be."""

    @property
    def name(self) -> str:
        return "RAWG"


async def test_two_providers_sharing_a_name_are_recorded_apart(owner, providers, client):
    user_id, _ = owner

    def rawg(provider_id: str, priority: int, **fields) -> StandInProvider:
        linked = _linked(provider_id, "rawg", priority, **fields)
        return _NamedRawg(linked.id, linked.namespace, linked.respond, priority=priority)

    providers(
        _linked("core.steam", "steam", 0, provider_ids={"steam": STEAM_ID}, title="Portal 2"),
        rawg("community.one.rawg", 10, developer="Valve"),
        rawg("community.two.rawg", 20, developer="Someone else", series="Portal"),
    )
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})

    await refresh(client, game_id)

    sources = await current_sources(game_id)
    assert sources["developer"] == ("RAWG (community.one.rawg)", "Valve")
    assert sources["series"] == ("RAWG (community.two.rawg)", "Portal")


# --- a re-import through the real Steam sync after merge and unmerge, both directions --------


def steam_library(monkeypatch, *titles: tuple[int, str]) -> None:
    """The Steam account owns `titles`; the real sync route, row locks and writes run."""
    monkeypatch.setattr(
        library_sync.steam_connection, "credentials", AsyncMock(return_value=("7656", "key"))
    )
    monkeypatch.setattr(library_sync.steam_connection, "recheck", AsyncMock(return_value=None))
    owned = [{"appid": appid, "name": name, "playtime_forever": 30} for appid, name in titles]
    monkeypatch.setattr(library_sync.steam, "get_owned_games", lambda *_: owned)


async def sync_steam(client) -> dict:
    response = await client.post("/api/library-sync/steam", params={"achievements": "later"})
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_steam_sync_reuses_the_steam_copy_whichever_side_of_a_merge_it_is(
    owner, providers, client, monkeypatch
):
    user_id, _ = owner
    providers(*four_providers())
    steam_game = await add_game(
        user_id,
        title="Portal 2",
        source="Steam",
        external_id=STEAM_ID,
        provider_ids={"steam": STEAM_ID},
        playtime_seconds=600,
    )
    other_copy = await add_game(user_id, title="Portal 2", source="manual", playtime_seconds=60)
    await refresh(client, steam_game)
    steam_library(monkeypatch, (int(STEAM_ID), "Portal 2"))

    # the Steam copy is first the merged-away member, then the one the other is merged into
    for source, target in ((steam_game, other_copy), (other_copy, steam_game)):
        async with SessionLocal() as db:
            await decisions.merge_identities(db, user_id, source, target)
            await db.commit()
        merged = await sync_steam(client)
        assert (merged["games_added"], merged["games_updated"]) == (0, 1)
        assert merged["enrich_game_ids"] == []
        async with SessionLocal() as db:
            # not marked separate, so the same pair can be merged the other way next
            await decisions.unmerge_identity(db, user_id, source, keep_separate=False)
            await db.commit()
        apart = await sync_steam(client)
        assert (apart["games_added"], apart["games_updated"]) == (0, 1)
        assert await games_of(user_id) == 2

    game = await stored(steam_game)
    # the sync's playtime reached the Steam copy only; the refreshed ids were kept
    assert game.playtime_seconds == 30 * 60
    assert game.provider_ids["igdb"] == IGDB_ID
    assert (await stored(other_copy)).playtime_seconds == 60


async def test_d2_preferring_a_provider_that_lost_the_merge_applies_at_once(
    owner, providers, client
):
    """PHASE5 D2: PUT field-preferences/developer for RAWG answered 200 ``applied: false`` and
    never took effect, because RAWG's developer lost to IGDB's and was never stored."""
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    await refresh(client, game_id)
    assert (await stored(game_id)).developer == "Valve"

    response = await client.put(
        f"/api/game/{game_id}/field-preferences/developer",
        json={"source_key": f"provider:{RAWG}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True
    assert response.json()["outcome"] == "applied"
    assert (await stored(game_id)).developer == "Valve (RAWG)"
    assert (await current_sources(game_id))["developer"] == (RAWG, "Valve (RAWG)")
    # the next refresh keeps the preferred value and changes nothing
    again = await refresh(client, game_id)
    assert again["changed_fields"] == []
    assert (await stored(game_id)).developer == "Valve (RAWG)"
    # RAWG changes its answer: the preference follows RAWG, not the merge winner
    providers(*four_providers(rawg={"developer": "Valve Software (RAWG)"}))
    moved = await refresh(client, game_id)
    assert moved["changed_fields"] == ["developer"]
    assert (await stored(game_id)).developer == "Valve Software (RAWG)"
    # clearing the preference hands the field back to the merge at the next refresh
    await client.delete(f"/api/game/{game_id}/field-preferences/developer")
    await refresh(client, game_id)
    assert (await stored(game_id)).developer == "Valve"


async def test_preferring_a_provider_with_no_value_yet_says_so_and_waits(owner, providers, client):
    user_id, _ = owner
    providers(*four_providers(rawg={"developer": None}))
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    await refresh(client, game_id)

    # RAWG reports no developer: the preference is stored and waits for a value
    response = await client.put(
        f"/api/game/{game_id}/field-preferences/developer",
        json={"source_key": f"provider:{RAWG}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["applied"] is False
    assert response.json()["outcome"] == "awaiting_value"
    assert (await stored(game_id)).developer == "Valve"
    # RAWG starts reporting one, still below IGDB in priority: the next refresh applies it
    providers(*four_providers())
    result = await refresh(client, game_id)
    assert "developer" in result["changed_fields"]
    assert (await stored(game_id)).developer == "Valve (RAWG)"
    assert (await current_sources(game_id))["developer"] == (RAWG, "Valve (RAWG)")


async def test_a_locked_field_reports_locked_and_keeps_the_candidates(owner, providers, client):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(
        user_id,
        provider_ids={"steam": STEAM_ID},
        developer="My own developer",
        locked_fields=["developer"],
    )
    await refresh(client, game_id)

    response = await client.put(
        f"/api/game/{game_id}/field-preferences/developer",
        json={"source_key": f"provider:{RAWG}"},
    )

    assert response.json() == {
        "field": "developer",
        "source_key": f"provider:{RAWG}",
        "applied": False,
        "outcome": "locked",
    }
    assert (await stored(game_id)).developer == "My own developer"
    # both providers' values are offered (neither is the game's value while locked)
    read = (await client.get(f"/api/game/{game_id}/field-sources")).json()["developer"]
    assert sorted(s["source_key"] for s in read["sources"]) == [
        f"provider:{RAWG}",
        "provider:core.igdb",
    ]


async def test_preferring_a_losing_providers_release_date_keeps_its_precision(
    owner, providers, client
):
    user_id, _ = owner
    # Steam's first release (a day) wins over RAWG's year-only one
    providers(
        *four_providers(
            steam={"first_release": MetadataRelease(date="2011-04-19", precision="day")},
            rawg={"first_release": MetadataRelease(date="2012", precision="year")},
        )
    )
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    await refresh(client, game_id)
    assert (await stored(game_id)).release_date == date(2011, 4, 19)

    response = await client.put(
        f"/api/game/{game_id}/field-preferences/release_date",
        json={"source_key": f"provider:{RAWG}"},
    )

    assert response.json()["applied"] is True
    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2012, 1, 1), "year")
    assert (await current_sources(game_id))["release_date"] == (RAWG, "2012")
    # a refresh keeps RAWG's year (not Steam's day, not the first of January as a day)
    again = await refresh(client, game_id)
    assert "release_date" not in again["changed_fields"]
    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2012, 1, 1), "year")


async def test_candidates_are_bounded_and_only_hold_preferable_fields(owner, providers, client):
    user_id, _ = owner
    huge = "x" * (field_provenance.MAX_CANDIDATE_CHARS + 10)
    providers(*four_providers(atlas={"description": huge, "developer": "Atlas developer"}))
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})

    await refresh(client, game_id)

    async with SessionLocal() as db:
        rows = list(
            await db.scalars(
                select(GameFieldValue).where(
                    GameFieldValue.game_id == game_id,
                    GameFieldValue.state == FieldValueState.CANDIDATE.value,
                )
            )
        )
    kept = {(row.field, row.source_label) for row in rows}
    # the over-long description is not kept; the other losing values are, one row each
    assert kept == {("developer", RAWG), ("developer", ATLAS)}
    assert {row.field for row in rows} <= PROVENANCE_FIELDS
    assert all(row.origin == FieldValueOrigin.METADATA.value for row in rows)
    # and a second refresh with the same answers adds nothing
    await refresh(client, game_id)
    async with SessionLocal() as db:
        again = await db.scalar(
            select(func.count()).where(
                GameFieldValue.game_id == game_id,
                GameFieldValue.state == FieldValueState.CANDIDATE.value,
            )
        )
    assert again == len(kept)


async def test_a_provider_that_stops_reporting_a_field_loses_its_candidate(
    owner, providers, client
):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    await refresh(client, game_id)

    # RAWG still answers, but no longer names a developer
    providers(*four_providers(rawg={"developer": None}))
    await refresh(client, game_id)

    read = (await client.get(f"/api/game/{game_id}/field-sources")).json()["developer"]
    assert [s["source_key"] for s in read["sources"]] == ["provider:core.igdb"]
