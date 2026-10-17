"""Playnite as a source of observations: evidence API, renames, locks, inventory (#100).

Payloads mirror what the extension builds from a real Playnite library entry
(`UnnamedTrackingSyncClient.ToGamePayload`): the original create/update routes
and the versioned `/api/playnite` evidence routes, over HTTP with a `utk_` key.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update

from src.database.models.game import Game, GameLink
from src.database.models.game_identity import (
    GameSourceObservation,
    GameSourceObservationLink,
    GameSourceObservationRevision,
)
from src.database.models.user import User
from src.database.session import SessionLocal
from tests.test_playnite_contract import _key_client, storage  # noqa: F401

NOW = 1_760_000_000


@pytest.fixture
async def clients():
    """Extension-style clients; a real payload carries links, which must be removed first."""
    stack: list = []
    yield lambda: _key_client(stack)
    for api, user_id in stack:
        await api.aclose()
        async with SessionLocal() as db:
            ids = select(Game.id).where(Game.user_id == user_id)
            await db.execute(delete(GameLink).where(GameLink.game_id.in_(ids)))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


def create_payload(guid: str, title: str = "Hades", **extra) -> dict:
    """The create body of a Playnite entry, as the extension serialises it."""
    body = {
        "title": title,
        "sort_title": title,
        "description": "Escape the underworld.",
        "release_date": "2020-09-17",
        "developer": "Supergiant Games",
        "publisher": "Supergiant Games",
        "tags": ["Roguelike", "Platform:PC (Windows)"],
        "features": ["Single Player"],
        "collections": ["Favourites"],
        "links": [{"label": "Store", "url": "https://store.example/hades"}],
        "source": "Steam",
        "favorite": True,
        "notes": "",
        "playtime_seconds": 36_000,
        "rating_overall": 9.5,
        "status": "PLAYING",
        "folder_location": f"playnite-{title.lower()}-{guid.replace('-', '')}",
        "playnite_guid": guid,
    }
    body.update(extra)
    return body


def entry(guid: str, **extra) -> dict:
    return {
        "playnite_id": guid,
        "name": "Hades",
        "platforms": ["PC (Windows)", "Nintendo Switch"],
        "release_date": "2020-09-17",
        "playtime_seconds": 36_000,
        "last_played_at": NOW,
        "installed": True,
        "owned": True,
        "favorite": True,
        "status": "PLAYING",
        "achievements_total": 49,
        "achievements_unlocked": 12,
        "modified_at": NOW,
        **extra,
    }


def batch(*games: dict, library_id: str | None = None, version: int = 1) -> dict:
    body: dict = {"contract_version": version, "games": list(games)}
    if library_id is not None:
        body["library_id"] = library_id
    return body


async def observation_of(user_id, guid: str, scope: str = "legacy") -> GameSourceObservation:
    async with SessionLocal() as db:
        row = await db.scalar(
            select(GameSourceObservation).where(
                GameSourceObservation.user_id == user_id,
                GameSourceObservation.external_id == guid,
            )
        )
        assert row is not None
        return row


async def count(model, user_id) -> int:
    async with SessionLocal() as db:
        return await db.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        )


async def import_game(playnite, guid: str, title: str = "Hades") -> str:
    created = await playnite.api.post("/api/game/create", json=create_payload(guid, title))
    assert created.status_code == 201, created.text
    return created.json()["id"]


# --- connection and version negotiation ------------------------------------------------------


async def test_contract_endpoint_negotiates_and_requires_a_key(clients):
    playnite = await clients()
    described = await playnite.api.get("/api/playnite/contract")
    assert described.status_code == 200, described.text
    body = described.json()
    assert body["supported_versions"] == [1] and body["current_version"] == 1
    assert {"observations", "complete-inventory", "last-played"} <= set(body["features"])
    anonymous = await playnite.api.get(
        "/api/playnite/contract", headers={"Authorization": "Bearer utk_wrong"}
    )
    assert anonymous.status_code in (401, 403)


async def test_unsupported_versions_fail_safe_without_touching_data(clients):
    playnite = await clients()
    guid = str(uuid4())
    await import_game(playnite, guid)
    for version in (0, 2, 99):
        refused = await playnite.api.post(
            "/api/playnite/observations", json=batch(entry(guid), version=version)
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["detail"]["error"] == "unsupported_contract_version"
        assert refused.json()["detail"]["supported"] == [1]
    inventory = await playnite.api.post(
        "/api/playnite/inventory", json={"contract_version": 2, "playnite_ids": []}
    )
    assert inventory.status_code == 400
    # the original create observation is all there is: nothing from the refused calls
    assert await count(GameSourceObservationRevision, playnite.user_id) == 1


async def test_malformed_batches_are_rejected(clients):
    playnite = await clients()
    guid = str(uuid4())
    post = playnite.api.post
    assert (await post("/api/playnite/observations", json=batch())).status_code == 422
    twice = batch(entry(guid), entry(guid))
    assert (await post("/api/playnite/observations", json=twice)).status_code == 422
    extra = batch(entry(guid, force_merge_with="x"))
    assert (await post("/api/playnite/observations", json=extra)).status_code == 422
    bad_id = batch(entry("not-a-guid"))
    assert (await post("/api/playnite/observations", json=bad_id)).status_code == 422
    bad_library = batch(entry(guid), library_id="no spaces")
    assert (await post("/api/playnite/observations", json=bad_library)).status_code == 422
    negative = batch(entry(guid, playtime_seconds=-1))
    assert (await post("/api/playnite/observations", json=negative)).status_code == 422


# --- evidence from a representative library entry --------------------------------------------


async def test_entry_fields_are_recorded_as_one_observation_and_replay_is_idempotent(clients):
    playnite = await clients()
    guid = str(uuid4())
    game_id = await import_game(playnite, guid)

    first = await playnite.api.post("/api/playnite/observations", json=batch(entry(guid)))
    assert first.status_code == 200, first.text
    assert first.json()["results"] == [
        {"playnite_id": guid, "game_id": game_id, "outcome": "updated"}
    ]
    row = await observation_of(playnite.user_id, guid)
    assert (row.observed_title, row.observed_platform) == ("Hades", "PC (Windows)")
    facts = row.observed_facts
    assert facts["playtime_seconds"] == 36_000 and facts["last_played_at"] == NOW
    assert facts["installed"] is True and facts["owned"] is True
    assert (facts["achievements_total"], facts["achievements_unlocked"]) == (49, 12)
    assert row.availability == "available"

    again = await playnite.api.post("/api/playnite/observations", json=batch(entry(guid)))
    assert again.json()["results"][0]["outcome"] == "unchanged"
    assert await count(GameSourceObservation, playnite.user_id) == 1
    assert await count(GameSourceObservationLink, playnite.user_id) == 1

    stored = (await playnite.api.get(f"/api/game/get/{game_id}")).json()
    assert stored["last_played_at"] == NOW
    listed = (await playnite.api.get("/api/game/list")).json()
    assert [g["id"] for g in listed] == [game_id]


async def test_older_report_never_rewinds_the_observation_or_last_played(clients):
    playnite = await clients()
    guid = str(uuid4())
    game_id = await import_game(playnite, guid)
    await playnite.api.post("/api/playnite/observations", json=batch(entry(guid)))
    late = entry(guid, playtime_seconds=1, last_played_at=NOW - 500, modified_at=NOW - 500)
    result = await playnite.api.post("/api/playnite/observations", json=batch(late))
    assert result.json()["results"][0]["outcome"] == "stale"
    row = await observation_of(playnite.user_id, guid)
    assert row.observed_facts["playtime_seconds"] == 36_000
    assert (await playnite.api.get(f"/api/game/get/{game_id}")).json()["last_played_at"] == NOW


async def test_unknown_and_trashed_entries_are_reported_never_created(clients):
    playnite = await clients()
    unknown, trashed = str(uuid4()), str(uuid4())
    trashed_id = await import_game(playnite, trashed, "Doom")
    assert (await playnite.api.delete(f"/api/game/delete/{trashed_id}")).status_code == 204
    result = await playnite.api.post(
        "/api/playnite/observations", json=batch(entry(unknown), entry(trashed))
    )
    assert result.json()["results"] == [
        {"playnite_id": unknown, "game_id": None, "outcome": "unlinked"},
        {"playnite_id": trashed, "game_id": trashed_id, "outcome": "in_trash"},
    ]
    assert (await playnite.api.get("/api/game/list")).json() == []
    async with SessionLocal() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(GameSourceObservation)
                .where(GameSourceObservation.external_id == unknown)
            )
        ) == 0


async def test_another_users_entry_is_never_attached(clients):
    owner, stranger = await clients(), await clients()
    guid = str(uuid4())
    await import_game(owner, guid)
    result = await stranger.api.post("/api/playnite/observations", json=batch(entry(guid)))
    assert result.json()["results"][0]["outcome"] == "unlinked"
    assert await count(GameSourceObservation, stranger.user_id) == 0


async def test_library_scopes_keep_installations_apart_on_one_game(clients):
    playnite = await clients()
    guid = str(uuid4())
    game_id = await import_game(playnite, guid)
    await playnite.api.post("/api/playnite/observations", json=batch(entry(guid)))
    other = await playnite.api.post(
        "/api/playnite/observations",
        json=batch(entry(guid, playtime_seconds=5), library_id="laptop"),
    )
    assert other.json()["results"][0]["game_id"] == game_id
    assert await count(GameSourceObservation, playnite.user_id) == 2
    assert len((await playnite.api.get("/api/game/list")).json()) == 1


# --- renames and user protection through the original routes ---------------------------------


async def test_playnite_renames_follow_without_locking_or_duplicating(clients):
    playnite = await clients()
    guid = str(uuid4())
    game_id = await import_game(playnite, guid)
    for title in ("Hades (2020)", "Hades II Prototype", "Hades"):
        patched = await playnite.api.patch(
            f"/api/game/update/{game_id}", json={"title": title, "sort_title": title}
        )
        assert patched.status_code == 200, patched.text
        stored = (await playnite.api.get(f"/api/game/get/{game_id}")).json()
        assert stored["title"] == title
        assert stored["locked_fields"] == []  # Playnite's change is not the user's protection
    row = await observation_of(playnite.user_id, guid)
    assert row.observed_title == "Hades" and row.revision >= 4
    assert len((await playnite.api.get("/api/game/list")).json()) == 1


async def test_user_locked_name_and_status_survive_a_playnite_sync(clients):
    playnite = await clients()
    guid = str(uuid4())
    game_id = await import_game(playnite, guid)
    async with SessionLocal() as db:
        await db.execute(
            update(Game)
            .where(Game.id == UUID(game_id))
            .values(title="My Hades", status="MASTERED", locked_fields=["status", "title"])
        )
        await db.commit()
    synced = await playnite.api.patch(
        f"/api/game/update/{game_id}",
        json={"title": "Hades (Renamed)", "status": "BACKLOG", "playtime_seconds": 72_000},
    )
    assert synced.status_code == 200, synced.text  # a locked name is kept, not a 403
    stored = (await playnite.api.get(f"/api/game/get/{game_id}")).json()
    assert (stored["title"], stored["status"]) == ("My Hades", "MASTERED")
    assert stored["playtime_seconds"] == 72_000
    assert sorted(stored["locked_fields"]) == ["status", "title"]
    # ...but Playnite's report is still recorded as the source's own evidence
    row = await observation_of(playnite.user_id, guid)
    assert row.observed_title == "Hades (Renamed)"
    assert row.observed_facts["status"] == "BACKLOG"


async def test_repeated_create_after_reconnect_returns_the_same_game(clients):
    playnite = await clients()
    guid = str(uuid4())
    game_id = await import_game(playnite, guid)
    for _ in range(3):
        again = await playnite.api.post("/api/game/create", json=create_payload(guid))
        assert again.status_code == 200 and again.json()["id"] == game_id
    assert await count(GameSourceObservation, playnite.user_id) == 1
    assert await count(Game, playnite.user_id) == 1


# --- partial inventories ---------------------------------------------------------------------


async def test_partial_pages_and_failed_pulls_never_make_anything_unavailable(clients):
    playnite = await clients()
    guids = [str(uuid4()) for _ in range(3)]
    for index, guid in enumerate(guids):
        await import_game(playnite, guid, f"Game{index}")
    await playnite.api.post(
        "/api/playnite/observations", json=batch(*[entry(g, name="G") for g in guids])
    )
    # a later page that reports one entry only (cancelled, paginated or filtered)
    await playnite.api.post("/api/playnite/observations", json=batch(entry(guids[0])))
    async with SessionLocal() as db:
        states = set(
            await db.scalars(
                select(GameSourceObservation.availability).where(
                    GameSourceObservation.user_id == playnite.user_id
                )
            )
        )
    assert states == {"available"}


async def test_complete_inventory_marks_absent_entries_and_a_return_restores_them(clients):
    playnite = await clients()
    kept, removed = str(uuid4()), str(uuid4())
    kept_game = await import_game(playnite, kept, "Kept")
    removed_game = await import_game(playnite, removed, "Removed")
    await playnite.api.post("/api/playnite/observations", json=batch(entry(kept), entry(removed)))
    done = await playnite.api.post(
        "/api/playnite/inventory", json={"contract_version": 1, "playnite_ids": [kept]}
    )
    assert done.json() == {"contract_version": 1, "applied": True, "marked_unavailable": 1}
    assert (await observation_of(playnite.user_id, removed)).availability == "unavailable"
    assert (await observation_of(playnite.user_id, kept)).availability == "available"
    # nothing is deleted: both games, both observations and both links remain
    assert {g["id"] for g in (await playnite.api.get("/api/game/list")).json()} == {
        kept_game,
        removed_game,
    }
    assert await count(GameSourceObservation, playnite.user_id) == 2
    assert await count(GameSourceObservationLink, playnite.user_id) == 2
    # an inventory sent twice is idempotent
    repeat = await playnite.api.post(
        "/api/playnite/inventory", json={"contract_version": 1, "playnite_ids": [kept]}
    )
    assert repeat.json()["marked_unavailable"] == 0
    # the entry comes back (Playnite re-added it): reported again, available again
    back = await playnite.api.post("/api/playnite/observations", json=batch(entry(removed)))
    assert back.status_code == 200
    assert (await observation_of(playnite.user_id, removed)).availability == "available"


async def test_inventory_for_an_unknown_library_changes_nothing(clients):
    playnite = await clients()
    guid = str(uuid4())
    await import_game(playnite, guid)
    await playnite.api.post("/api/playnite/observations", json=batch(entry(guid)))
    result = await playnite.api.post(
        "/api/playnite/inventory",
        json={"contract_version": 1, "library_id": "nowhere", "playnite_ids": []},
    )
    assert result.json()["applied"] is False
    assert (await observation_of(playnite.user_id, guid)).availability == "available"


# --- import -> merge -> sync -> manual edit -> sync -> split ---------------------------------


async def test_import_merge_sync_manual_edit_sync_split_keeps_everything(clients):
    playnite = await clients()
    api = playnite.api
    guid = str(uuid4())
    playnite_game = await import_game(playnite, guid)
    manual = (
        await api.post(
            "/api/game/create",
            json={"title": "Hades", "folder_location": "Hades-manual", "status": "BACKLOG"},
        )
    ).json()["id"]
    await api.post("/api/playnite/observations", json=batch(entry(guid)))

    merged = await api.post(
        "/api/game-identity/merge", json={"source_id": manual, "target_id": playnite_game}
    )
    assert merged.status_code == 200, merged.text
    for game in (playnite_game, manual):
        identity = (await api.get(f"/api/game-identity/games/{game}")).json()
        assert identity["identity_id"] == playnite_game  # one umbrella
    metrics = (await api.get(f"/api/game-identity/games/{playnite_game}/metrics")).json()
    playtime = metrics["metrics"]["playtime"]
    assert playtime["effective"] == 36_000 and len(playtime["counters"]) == 1  # one counter

    # sync again with more playtime; the merge and the counter stay one
    more = entry(guid, playtime_seconds=40_000, modified_at=NOW + 10)
    await api.post("/api/playnite/observations", json=batch(more))
    await api.patch(f"/api/game/update/{playnite_game}", json={"playtime_seconds": 40_000})
    playtime = (await api.get(f"/api/game-identity/games/{playnite_game}/metrics")).json()[
        "metrics"
    ]["playtime"]
    assert playtime["effective"] == 40_000 and len(playtime["counters"]) == 1

    # the user corrects playtime and locks the name by hand
    corrected = await api.put(
        f"/api/game-identity/games/{playnite_game}/metrics/playtime", json={"seconds": 50_000}
    )
    assert corrected.json()["metrics"]["playtime"]["effective"] == 50_000
    async with SessionLocal() as db:
        await db.execute(
            update(Game)
            .where(Game.id == UUID(playnite_game))
            .values(title="Hades (mine)", status="MASTERED", locked_fields=["status", "title"])
        )
        await db.commit()

    # Playnite syncs again, with a rename, a different status and newer playtime
    newer = entry(guid, name="Hades GOTY", playtime_seconds=41_000, modified_at=NOW + 20)
    await api.post("/api/playnite/observations", json=batch(newer))
    synced = await api.patch(
        f"/api/game/update/{playnite_game}",
        json={"title": "Hades GOTY", "status": "PLAYED", "playtime_seconds": 41_000},
    )
    assert synced.status_code == 200, synced.text
    stored = (await api.get(f"/api/game/get/{playnite_game}")).json()
    assert (stored["title"], stored["status"]) == ("Hades (mine)", "MASTERED")
    playtime = (await api.get(f"/api/game-identity/games/{playnite_game}/metrics")).json()[
        "metrics"
    ]["playtime"]
    assert playtime["effective"] == 51_000  # the persistent correction rides on the new reading
    assert len(playtime["counters"]) == 1
    assert len((await api.get("/api/game/list")).json()) == 2  # no duplicate was created

    # split: both games and every observation remain, linked as before
    unmerged = await api.post("/api/game-identity/unmerge", json={"game_id": manual})
    assert unmerged.status_code == 200, unmerged.text
    assert (await api.get(f"/api/game-identity/games/{manual}")).json()["identity_id"] == manual
    after = (await api.get(f"/api/game-identity/games/{playnite_game}/metrics")).json()
    assert after["metrics"]["playtime"]["effective"] == 51_000
    row = await observation_of(playnite.user_id, guid)
    assert row.observed_title == "Hades GOTY" and row.availability == "available"
    for expected in ("updated", "unchanged"):  # a retried delivery changes nothing
        resync = await api.post("/api/playnite/observations", json=batch(newer))
        assert resync.json()["results"][0]["outcome"] == expected
    assert await count(GameSourceObservation, playnite.user_id) == 2  # Playnite + manual
