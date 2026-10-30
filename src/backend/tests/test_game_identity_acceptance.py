"""#91 scenario matrix as end-to-end tests over the real HTTP surface (#103).

Playnite acts as the extension does (a `utk_` key, the original create/update routes and
the versioned evidence API). User decisions use the identity routes. Steam, a source
the host reads server-side, is recorded through the ingestion service exactly as its sync
does. Every test names the matrix row it covers.
"""

import asyncio
import logging
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, update

from src.api.routes.library_sync import _replace_achievements
from src.database.models.game import Game
from src.database.models.game_identity import (
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
)
from src.database.session import SessionLocal
from src.features.game_identity_backfill import runner
from src.features.game_ingestion import legacy
from src.features.game_ingestion.service import disconnect_connection
from src.features.imports.library_games import get_or_create_game
from tests.test_game_identity_backfill_playnite import forget_identity_rows
from tests.test_game_identity_matching import PLAYNITE as MATCH_PLAYNITE
from tests.test_game_identity_matching import STEAM as MATCH_STEAM
from tests.test_game_identity_matching import record
from tests.test_game_names import sync as steam_sync
from tests.test_playnite_contract import storage  # noqa: F401
from tests.test_playnite_observations import (
    NOW,
    batch,
    clients,  # noqa: F401
    count,
    create_payload,
    entry,
)

HOUR = 3600
ID = "/api/game-identity"


def metrics_of(body, metric="playtime"):
    return body["metrics"][metric]


async def metrics(api, game):
    got = await api.get(f"{ID}/games/{game}/metrics")
    assert got.status_code == 200, got.text
    return got.json()


async def manual_game(api, title, **extra):
    made = await api.post(
        "/api/game/create", json={"title": title, "folder_location": f"m-{uuid4().hex}", **extra}
    )
    assert made.status_code == 201, made.text
    return made.json()["id"]


def create_body(guid, title):
    return create_payload(
        guid, title, source="Microsoft Store", folder_location=f"pn-{guid.replace('-', '')}"
    )


async def import_playnite_entry(playnite, guid, title):
    """The extension's create call for a library entry (a non-Steam library source)."""
    created = await playnite.api.post("/api/game/create", json=create_body(guid, title))
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def playnite_game(playnite, guid, title, hours, **extra):
    game = await import_playnite_entry(playnite, guid, title)
    sent = entry(guid, name=title, playtime_seconds=hours * HOUR, **extra)
    posted = await playnite.api.post("/api/playnite/observations", json=batch(sent))
    assert posted.status_code == 200, posted.text
    return game


async def live_games(api):
    return (await api.get("/api/game/list")).json()


async def tally(user_id):
    return (
        await count(Game, user_id),
        await count(GameSourceObservation, user_id),
        await count(GameSourceObservationLink, user_id),
    )


async def merge(api, source, target):
    got = await api.post(f"{ID}/merge", json={"source_id": source, "target_id": target})
    assert got.status_code == 200, got.text
    return got.json()


async def group_playtime(api, target_game, source_game):
    """Declare that two games' playtime counters report the same thing."""
    found = metrics_of(await metrics(api, target_game))["counters"]
    assert len(found) == 2, found  # safe default: one counter per reporting observation
    got = await api.post(
        f"{ID}/counters/{found[0]['counter_id']}/group",
        json={"source_counter_id": found[1]["counter_id"]},
    )
    assert got.status_code == 200, got.text


# --- 1. Playnite + manual Minecraft: one counter, no duplicate, no double count -------------


async def test_1_playnite_and_manual_merge_into_one_counter_across_resyncs(clients):  # noqa: F811
    playnite = await clients()
    api = playnite.api
    guid = str(uuid4())
    imported = await playnite_game(playnite, guid, "Minecraft", 100)
    by_hand = await manual_game(api, "Minecraft", playtime_seconds=100 * HOUR)
    before = await tally(playnite.user_id)

    await merge(api, by_hand, imported)
    await group_playtime(api, imported, by_hand)  # the user says: same counter
    both = metrics_of(await metrics(api, imported))
    assert len(both["counters"]) == 1
    assert both["effective"] == 100 * HOUR  # a duplicate report is not doubled

    # Reports made within one second tie on freshness; the user ranks Playnite first.
    (counter,) = both["counters"]
    playnite_member = next(m for m in counter["members"] if m["source"] == "playnite")
    ranked = await api.put(
        f"{ID}/counters/{counter['counter_id']}/members/{playnite_member['observation_id']}/priority",
        json={"priority": 0},
    )
    assert ranked.status_code == 204, ranked.text

    for _ in range(3):  # repeated syncs, each older or equal, then newer
        again = await api.post(
            "/api/playnite/observations", json=batch(entry(guid, name="Minecraft"))
        )
        assert again.status_code == 200
    newer = entry(guid, name="Minecraft", playtime_seconds=110 * HOUR, modified_at=NOW + 50)
    await api.post("/api/playnite/observations", json=batch(newer))
    after = metrics_of(await metrics(api, imported))
    assert len(after["counters"]) == 1 and after["effective"] == 110 * HOUR
    assert await tally(playnite.user_id) == before  # no new game, observation or link
    assert len(await live_games(api)) == 2  # the merge hides nothing and creates nothing


async def steam_game(user_id, external_id, title, hours=None):
    """A Steam library sync of one title (what the library sync records for it)."""
    async with SessionLocal() as db:
        game, _ = await get_or_create_game(db, user_id, title, "Steam", external_id=external_id)
        facts = {}
        if hours is not None:
            game.playtime_seconds = hours * HOUR
            facts = {"playtime_seconds": hours * HOUR}
        await legacy.record_observations(
            db,
            user_id,
            legacy.STEAM,
            [(game.id, legacy.build_observation(external_id, title=title, **facts))],
        )
        await db.commit()
        return game.id


# --- 2. Steam Minecraft is a separate counter; the umbrella sums independent counters -------


async def test_2_steam_counter_stays_separate_and_the_umbrella_sums_independent_counters(
    clients,  # noqa: F811
):
    playnite = await clients()
    api = playnite.api
    guid = str(uuid4())
    imported = await playnite_game(playnite, guid, "Minecraft", 100)
    by_hand = await manual_game(api, "Minecraft", playtime_seconds=100 * HOUR)
    await merge(api, by_hand, imported)
    await group_playtime(api, imported, by_hand)
    steam = str(await steam_game(playnite.user_id, "1672970", "Minecraft", hours=30))
    await merge(api, steam, imported)

    umbrella = metrics_of(await metrics(api, imported))
    assert len(umbrella["counters"]) == 2  # the merged Playnite/manual counter + Steam's own
    assert umbrella["effective"] == (100 + 30) * HOUR
    by_source = {c["selected_source"]: c["effective"] for c in umbrella["counters"]}
    assert by_source["steam"] == 30 * HOUR and sum(by_source.values()) == (100 + 30) * HOUR
    same = metrics_of(await metrics(api, steam))
    assert same["effective"] == umbrella["effective"]  # any member shows the same umbrella


# --- 3. split restores every source and the metrics of each side ---------------------------


async def test_3_split_is_reversible_and_recovers_source_data_and_metrics(clients):  # noqa: F811
    playnite = await clients()
    api = playnite.api
    guid = str(uuid4())
    imported = await playnite_game(playnite, guid, "Minecraft", 100)
    steam = str(await steam_game(playnite.user_id, "1672970", "Minecraft", hours=30))
    before = await tally(playnite.user_id)
    assert metrics_of(await metrics(api, imported))["effective"] == 100 * HOUR
    assert metrics_of(await metrics(api, steam))["effective"] == 30 * HOUR

    await merge(api, steam, imported)
    assert metrics_of(await metrics(api, imported))["effective"] == 130 * HOUR
    split = await api.post(f"{ID}/unmerge", json={"game_id": steam})
    assert split.status_code == 200, split.text
    assert metrics_of(await metrics(api, imported))["effective"] == 100 * HOUR
    assert metrics_of(await metrics(api, steam))["effective"] == 30 * HOUR
    assert await tally(playnite.user_id) == before  # nothing was deleted, moved or duplicated
    assert len(await live_games(api)) == 2
    assert (await api.get(f"{ID}/games/{steam}")).json()["identity_id"] == steam
    # the pair is remembered as separate; the user can still choose to merge it again
    again = await api.post(
        f"{ID}/merge", json={"source_id": steam, "target_id": imported, "replace_separate": True}
    )
    assert again.status_code == 200, again.text
    assert metrics_of(await metrics(api, imported))["effective"] == 130 * HOUR


# --- 4. Java / Bedrock as versions: separate, grouped, nothing lost ------------------------


async def test_4_java_and_bedrock_display_separately_then_grouped_without_losing_tracking(
    clients,  # noqa: F811
):
    playnite = await clients()
    api = playnite.api
    java = await playnite_game(playnite, str(uuid4()), "Minecraft Java Edition", 40)
    bedrock = await playnite_game(playnite, str(uuid4()), "Minecraft Bedrock Edition", 15)
    await merge(api, bedrock, java)

    def shown(layout):
        return layout["mode"], {m["game_id"] for g in layout["groups"] for m in g["members"]}

    layout = (await api.get(f"{ID}/library")).json()
    assert shown(layout) == ("grouped", {bedrock})
    assert layout["groups"][0]["umbrella_id"] == java
    assert metrics_of(await metrics(api, java))["effective"] == 55 * HOUR

    separate = await api.patch("/api/preferences", json={"library_version_display": "separate"})
    assert separate.status_code == 200, separate.text
    layout = (await api.get(f"{ID}/library")).json()
    assert layout["mode"] == "separate"  # the library lists every version on its own
    assert len(await live_games(api)) == 2  # both versions are listed on their own
    assert metrics_of(await metrics(api, bedrock))["effective"] == 55 * HOUR  # tracking intact

    grouped = await api.patch("/api/preferences", json={"library_version_display": "grouped"})
    assert grouped.status_code == 200
    assert shown((await api.get(f"{ID}/library")).json()) == ("grouped", {bedrock})
    overview = (await api.get(f"{ID}/games/{bedrock}/overview")).json()
    assert overview["aggregate"]["version_count"] == 2
    own = {v["game_id"]: v["playtime_seconds"] for v in overview["versions"]}
    assert own == {java: 40 * HOUR, bedrock: 15 * HOUR}  # per-version values stay distinct

    layout = (await api.get(f"{ID}/library")).json()
    edge = layout["groups"][0]["members"][0]["relationship_id"]
    popped = await api.put(f"{ID}/relationships/{edge}/display", json={"display": "link"})
    assert popped.status_code == 200 and popped.json()["display"] == "link"
    assert (await api.get(f"{ID}/library")).json()["groups"][0]["members"][0][
        "display"
    ] == "separate"


# --- 5. a user-defined relationship between otherwise unrelated games ----------------------


async def test_5_user_defined_relationships_follow_the_configured_display(clients):  # noqa: F811
    playnite = await clients()
    api = playnite.api
    minecraft = await manual_game(api, "Minecraft")
    story = await manual_game(api, "Totally Different Title")
    made = await api.post(
        f"{ID}/relationships",
        json={"kind": "related_to", "from_id": story, "to_id": minecraft},
    )
    assert made.status_code == 201, made.text
    related = (await api.get(f"{ID}/library")).json()["related"]
    assert [r["kind"] for r in related] == ["related_to"]
    default_display = related[0]["display"]
    rules = {"library_relationship_display": {"related_to": "nest"}}
    assert (await api.patch("/api/preferences", json=rules)).status_code == 200
    assert (await api.get(f"{ID}/library")).json()["related"][0]["display"] == "nest"
    assert default_display != "nest"
    pinned = await api.put(
        f"{ID}/relationships/{made.json()['id']}/display", json={"display": "hidden"}
    )
    assert pinned.status_code == 200
    assert (await api.get(f"{ID}/library")).json()["related"][0]["display"] == "hidden"
    # an unusual combination is the user's choice and does not merge tracking
    assert (await api.get(f"{ID}/games/{story}")).json()["identity_id"] == story
    ended = await api.post(f"{ID}/relationships/{made.json()['id']}/end")
    assert ended.status_code == 200
    assert (await api.get(f"{ID}/library")).json()["related"] == []
    assert len(await live_games(api)) == 2


# --- 6. automatic merge is explained and undoable; rejected pairs are never re-suggested ----


async def test_6_automatic_merge_can_be_undone_and_is_not_proposed_again(clients):  # noqa: F811
    playnite = await clients()
    api, user = playnite.api, playnite.user_id
    a = await record(user, MATCH_STEAM, "10")
    b = await record(user, MATCH_PLAYNITE, "g")  # identical evidence: the strict rule applies
    ran = await api.post(f"{ID}/review/run")
    assert ran.status_code == 200 and ran.json()["associated"] == 1, ran.text
    banner = (await api.get(f"{ID}/review/automatic")).json()["merges"]
    assert len(banner) == 1 and banner[0]["reason"] and banner[0]["evidence"]
    first = (await api.get(f"{ID}/games/{a}")).json()["identity_id"]
    assert first == (await api.get(f"{ID}/games/{b}")).json()["identity_id"]
    undone = await api.post(f"{ID}/review/automatic/{banner[0]['id']}/undo")
    assert undone.status_code == 204, undone.text
    assert (await api.get(f"{ID}/games/{a}")).json()["identity_id"] != (
        await api.get(f"{ID}/games/{b}")
    ).json()["identity_id"]
    rerun = (await api.post(f"{ID}/review/run")).json()
    assert (rerun["associated"], rerun["suggested"]) == (0, 0)  # undone means never again
    assert (await api.get(f"{ID}/review/automatic")).json()["merges"] == []


async def test_6_rejecting_a_suggestion_is_final_but_a_third_record_is_reviewed(clients):  # noqa: F811
    playnite = await clients()
    api, user = playnite.api, playnite.user_id
    a = await record(user, MATCH_STEAM, "10", playtime=100)
    b = await record(user, MATCH_PLAYNITE, "g", playtime=200)  # similar, not identical
    assert (await api.post(f"{ID}/review/run")).json()["suggested"] == 1
    (queued,) = (await api.get(f"{ID}/review/queue")).json()["entries"]
    kept = await api.post(f"{ID}/review/queue/{queued['id']}/keep-separate")
    assert kept.status_code == 204, kept.text
    assert (await api.post(f"{ID}/review/run")).json()["suggested"] == 0
    assert (await api.get(f"{ID}/review/queue")).json()["entries"] == []
    gog = MATCH_STEAM.model_copy(
        update={"source": "gog", "source_kind": "plugin", "source_scope": "acct"}
    )
    third = await record(user, gog, "77", playtime=300)
    assert (await api.post(f"{ID}/review/run")).json()["suggested"] == 2
    pairs = (await api.get(f"{ID}/review/queue")).json()["entries"]
    assert all(str(third) in (e["first"]["id"], e["second"]["id"]) for e in pairs)
    seen = {e["first"]["id"] for e in pairs} | {e["second"]["id"] for e in pairs}
    assert seen == {str(a), str(b), str(third)}


# --- 7. placeholder wishlist name is replaced, nothing else changes ------------------------


async def test_7_placeholder_name_is_replaced_and_tracking_and_source_ids_are_kept(clients):  # noqa: F811
    playnite = await clients()
    api, user = playnite.api, playnite.user_id
    wished, created = await steam_sync(user, "620", "Steam App 620")
    assert created
    patched = await api.patch(
        f"/api/game/update/{wished}", json={"playtime_seconds": 90, "status": "BACKLOG"}
    )
    assert patched.status_code == 200, patched.text
    same, created = await steam_sync(user, "620", "Portal 2")
    assert (same, created) == (wished, False)
    stored = (await api.get(f"/api/game/get/{wished}")).json()
    assert (stored["title"], stored["playtime_seconds"]) == ("Portal 2", 90)
    async with SessionLocal() as db:
        assert await db.scalar(select(Game.external_id).where(Game.id == wished)) == "620"
    assert stored["status"] == "BACKLOG" and stored["locked_fields"] == []
    await steam_sync(user, "620", "Steam App 620")  # a worse name later does not undo it
    assert (await api.get(f"/api/game/get/{wished}")).json()["title"] == "Portal 2"
    provenance = (await api.get(f"{ID}/names/games/{wished}")).json()
    assert provenance["title"] == "Portal 2" and not provenance["locked"]
    assert len(await live_games(api)) == 1


# --- 8. a stable Playnite ID gets a legitimate new name --------------------------------------


async def test_8_stable_playnite_id_rename_is_reviewed_without_a_duplicate(clients):  # noqa: F811
    playnite = await clients()
    api = playnite.api
    guid = str(uuid4())
    game = await import_playnite_entry(playnite, guid, "Hades")
    async with SessionLocal() as db:  # the user named it themselves
        await db.execute(update(Game).where(Game.id == UUID(game)).values(locked_fields=["title"]))
        await db.commit()
    renamed = await api.patch(f"/api/game/update/{game}", json={"title": "Hades (2020)"})
    assert renamed.status_code == 200
    assert (await api.get(f"/api/game/get/{game}")).json()["title"] == "Hades"
    reviews = (await api.get(f"{ID}/names/reviews")).json()
    assert [(r["current_title"], r["proposed_title"]) for r in reviews] == [
        ("Hades", "Hades (2020)")
    ]
    again = await api.post("/api/game/create", json=create_body(guid, "Hades (2020)"))
    assert again.status_code == 200 and again.json()["id"] == game  # retry: same game
    assert len((await api.get(f"{ID}/names/reviews")).json()) == 1
    accepted = await api.post(f"{ID}/names/reviews/{reviews[0]['id']}/update")
    assert accepted.status_code == 200 and accepted.json()["title"] == "Hades (2020)"
    stored = (await api.get(f"/api/game/get/{game}")).json()
    assert stored["playnite_guid"] == guid and stored["title"] == "Hades (2020)"
    assert len(await live_games(api)) == 1
    assert await count(GameSourceObservation, playnite.user_id) == 1


# --- 9 + 10. manual tracking survives sync; completion and achievements are per version ------


async def test_9_manual_achievements_and_completion_survive_steam_and_playnite_sync(clients):  # noqa: F811
    playnite = await clients()
    api = playnite.api
    guid = str(uuid4())
    game = await playnite_game(playnite, guid, "Obscure Game", 5, status="PLAYING")
    made = await api.post(
        f"{ID}/games/{game}/manual-achievements",
        json={"title": "Find the key", "achieved": True, "achieved_at": 1_700_000_000},
    )
    assert made.status_code == 201, made.text
    done = await api.put(f"{ID}/games/{game}/completion", json={"status": "MASTERED"})
    assert done.status_code == 200, done.text

    for round_ in range(3):  # Playnite syncs a different status and achievement counts
        sent = entry(
            guid,
            name="Obscure Game",
            status="BACKLOG",
            achievements_total=10,
            achievements_unlocked=round_,
            modified_at=NOW + 100 + round_,
        )
        assert (await api.post("/api/playnite/observations", json=batch(sent))).status_code == 200
        await api.patch(f"/api/game/update/{game}", json={"status": "PLAYING"})
        async with SessionLocal() as db:  # and a Steam sync replaces the provider rows
            await _replace_achievements(
                db,
                UUID(game),
                "Steam",
                [{"external_id": "9", "name": "Synced", "unlocked": bool(round_ % 2)}],
            )
            await db.commit()
    assert (await api.get(f"/api/game/get/{game}")).json()["status"] == "MASTERED"
    achievements = (await api.get(f"{ID}/games/{game}/tracking")).json()["achievements"]
    assert [m["title"] for m in achievements["manual"]] == ["Find the key"]
    assert achievements["manual"][0]["achieved"]
    assert achievements["manual"][0]["achieved_at"] == 1_700_000_000
    assert achievements["umbrella"]["total"] == 2 and achievements["umbrella"]["manual"] == 1
    assert (await api.get(f"{ID}/games/{game}/completion/history")).json()
    released = await api.delete(f"{ID}/games/{game}/completion")
    assert released.status_code == 200  # releasing lets imports move the status again


async def test_10_completion_and_achievements_across_versions_never_double_count(clients):  # noqa: F811
    playnite = await clients()
    api = playnite.api
    pc = await playnite_game(playnite, str(uuid4()), "Game PC", 10)
    deck = await playnite_game(playnite, str(uuid4()), "Game Deck", 3)
    await merge(api, deck, pc)
    async with SessionLocal() as db:
        for game, rows in (
            (pc, [("1", "Win", True), ("2", "Lose", False)]),
            (deck, [("1", "Win", True), ("2", "Lose", True), ("3", "Deck only", False)]),
        ):
            await _replace_achievements(
                db,
                UUID(game),
                "Steam",
                [{"external_id": e, "name": n, "unlocked": u} for e, n, u in rows],
            )
        await db.commit()
    await api.put(f"{ID}/games/{pc}/completion", json={"status": "BEATEN"})

    overview = (await api.get(f"{ID}/games/{deck}/overview")).json()
    assert overview["aggregate"]["completion"]["summary"] == "Completed on 1 of 2 tracked versions"
    tracked = (await api.get(f"{ID}/games/{deck}/achievements")).json()
    assert tracked["umbrella"]["total"] == 3 and tracked["umbrella"]["unlocked"] == 2
    per_version = {v["game_id"]: (v["total"], v["unlocked"]) for v in tracked["versions"]}
    assert per_version == {pc: (2, 1), deck: (3, 2)}  # per-version lists keep their own sets
    again = (await api.get(f"{ID}/games/{deck}/achievements")).json()
    assert again["umbrella"] == tracked["umbrella"]  # a repeat read or sync changes nothing


# --- 11. disconnecting a source and failed or partial imports never imply deletion -----------


async def test_11_disconnect_and_partial_imports_keep_history(clients):  # noqa: F811
    playnite = await clients()
    api, user = playnite.api, playnite.user_id
    guids = [str(uuid4()) for _ in range(3)]
    games = [await playnite_game(playnite, g, f"Game {i}", 10 + i) for i, g in enumerate(guids)]
    steam = await steam_game(user, "440", "Team Fortress 2", hours=7)
    before = await tally(user)

    bad_version = batch(entry(guids[0]), version=9)
    assert (await api.post("/api/playnite/observations", json=bad_version)).status_code == 400
    negative = batch(entry(guids[0], playtime_seconds=-1))
    assert (await api.post("/api/playnite/observations", json=negative)).status_code == 422
    await api.post("/api/playnite/observations", json=batch(entry(guids[0])))  # a one-entry page
    unknown_library = await api.post(
        "/api/playnite/inventory",
        json={"contract_version": 1, "library_id": "elsewhere", "playnite_ids": []},
    )
    assert unknown_library.json()["applied"] is False
    assert await tally(user) == before

    done = await api.post(
        "/api/playnite/inventory", json={"contract_version": 1, "playnite_ids": guids[:2]}
    )
    assert done.json()["marked_unavailable"] == 1  # flagged, not deleted
    assert await tally(user) == before
    assert metrics_of(await metrics(api, games[2]))["effective"] == 12 * HOUR

    async with SessionLocal() as db:
        assert await disconnect_connection(db, user, legacy.STEAM)
        await db.commit()
    assert await tally(user) == before
    assert metrics_of(await metrics(api, str(steam)))["effective"] == 7 * HOUR
    async with SessionLocal() as db:
        flagged = (
            await db.scalars(
                select(GameSourceConnection.disconnected_at).where(
                    GameSourceConnection.user_id == user, GameSourceConnection.source == "steam"
                )
            )
        ).all()
    assert flagged and all(stamp is not None for stamp in flagged)
    assert len(await live_games(api)) == 4


# --- 12. migration of a populated library, repeated, then a Playnite sync --------------------


async def test_12_backfill_of_a_populated_library_is_repeatable_and_playnite_keeps_working(
    clients,  # noqa: F811
):
    playnite = await clients()
    api, user = playnite.api, playnite.user_id
    guid = str(uuid4())
    imported = await import_playnite_entry(playnite, guid, "Minecraft")
    by_hand = await manual_game(api, "Minecraft", playtime_seconds=3 * HOUR, notes="my world")
    await merge(api, by_hand, imported)
    wished = await steam_game(user, "12345", "Steam App 12345")
    diamonds = await api.post(
        f"{ID}/games/{imported}/manual-achievements", json={"title": "Diamonds", "achieved": True}
    )
    assert diamonds.status_code == 201
    await api.put(f"{ID}/games/{imported}/completion", json={"status": "BEATEN"})
    await forget_identity_rows(user)  # the library as it was before the identity model

    def snapshot(games):
        return {
            g["id"]: (g["title"], g["playtime_seconds"], g["status"], g["notes"]) for g in games
        }

    before = snapshot(await live_games(api))
    first = await api.post(f"{ID}/backfill?wait=true")
    assert first.status_code == 202 and first.json()["status"] == "completed", first.text
    created = await tally(user)
    for _ in range(2):  # repeated and resumed runs change nothing
        repeat = await api.post(f"{ID}/backfill?wait=true")
        assert repeat.json()["status"] == "completed"
        assert repeat.json()["counts"].get("observations_created", 0) == 0
        assert await tally(user) == created
    assert snapshot(await live_games(api)) == before  # no title, playtime, status or note changed
    merged = (await api.get(f"{ID}/games/{by_hand}")).json()["identity_id"]
    assert merged == imported
    tracking = (await api.get(f"{ID}/games/{imported}/tracking")).json()
    assert [m["title"] for m in tracking["achievements"]["manual"]] == ["Diamonds"]
    assert (await api.get(f"/api/game/get/{imported}")).json()["status"] == "BEATEN"
    assert (await api.get(f"/api/game/get/{wished}")).status_code == 200

    sync = await api.post(
        "/api/playnite/observations",
        json=batch(entry(guid, name="Minecraft", playtime_seconds=9 * HOUR)),
    )
    assert sync.status_code == 200 and sync.json()["results"][0]["game_id"] == imported
    assert len(await live_games(api)) == 3  # extended, never duplicated
    assert (await api.post(f"{ID}/backfill?wait=true")).json()["status"] == "completed"
    assert len(await live_games(api)) == 3


# --- 13. user isolation across every identity and manual-tracking route -----------------------

# Routes that act on the signed-in user's own data without naming anyone else's record.
OWN_DATA_ONLY = {
    ("get", "/api/game-identity/backfill"),
    ("post", "/api/game-identity/backfill"),
    ("get", "/api/game-identity/backfill/plan"),
    ("get", "/api/game-identity/decisions"),
    ("get", "/api/game-identity/library"),
    ("post", "/api/game-identity/names/repair"),
    ("get", "/api/game-identity/names/reviews"),
    ("get", "/api/game-identity/review/automatic"),
    ("get", "/api/game-identity/review/queue"),
    ("post", "/api/game-identity/review/run"),
    ("get", "/api/playnite/contract"),
    ("post", "/api/playnite/inventory"),
    ("post", "/api/playnite/observations"),
}


async def owner_world(playnite):
    """A merged pair with a counter, manual achievement, relationship, reviews and decisions."""
    api, user = playnite.api, playnite.user_id
    imported = await playnite_game(playnite, str(uuid4()), "Minecraft", 10)
    by_hand = await manual_game(api, "Minecraft", playtime_seconds=3 * HOUR)
    other = await manual_game(api, "Other")
    await merge(api, by_hand, imported)
    achievement = (
        await api.post(f"{ID}/games/{imported}/manual-achievements", json={"title": "Mine"})
    ).json()["id"]
    relationship = (
        await api.post(
            f"{ID}/relationships", json={"kind": "related_to", "from_id": other, "to_id": imported}
        )
    ).json()["id"]
    counters = metrics_of(await metrics(api, imported))["counters"]
    await api.put(f"{ID}/games/{imported}/metrics/playtime", json={"seconds": 11 * HOUR})
    steam_a = await record(user, MATCH_STEAM, "10")
    await record(user, MATCH_PLAYNITE, "g")
    await api.post(f"{ID}/review/run")
    auto = (await api.get(f"{ID}/review/automatic")).json()["merges"][0]["id"]
    near_a = await record(user, MATCH_STEAM.model_copy(update={"source": "gog"}), "88", playtime=5)
    await record(user, MATCH_PLAYNITE.model_copy(update={"source_scope": "l2"}), "h", playtime=9)
    await api.post(f"{ID}/review/run")
    queued = (await api.get(f"{ID}/review/queue")).json()["entries"][0]["id"]
    async with SessionLocal() as db:
        observation = await db.scalar(
            select(GameSourceObservation.id).where(
                GameSourceObservation.user_id == user,
                GameSourceObservation.external_id == str(near_a),
            )
        ) or await db.scalar(
            select(GameSourceObservation.id).where(GameSourceObservation.user_id == user)
        )
        wished, _ = await steam_sync(user, "620", "Steam App 620")
        await db.execute(update(Game).where(Game.id == wished).values(locked_fields=["title"]))
        await db.commit()
    await steam_sync(user, "620", "Portal 2")
    name_review = (await api.get(f"{ID}/names/reviews")).json()[0]["id"]
    return {
        "game": imported, "other": other, "achievement": achievement, "relationship": relationship,
        "counter": counters[0]["counter_id"], "observation": str(observation), "auto": auto,
        "queued": queued, "name_review": name_review, "steam": str(steam_a),
    }  # fmt: skip


def foreign_calls(w):
    """Every route that names a record, called with the other user's IDs."""
    g, o = w["game"], w["other"]
    base = "/api/game-identity"
    return [
        ("post", f"{base}/merge", {"source_id": o, "target_id": g}),
        ("post", f"{base}/unmerge", {"game_id": g}),
        ("post", f"{base}/separate", {"first_id": g, "second_id": o}),
        ("post", f"{base}/observations/{w['observation']}/move", {"game_id": o}),
        ("get", f"{base}/games/{g}", None),
        ("get", f"{base}/games/{g}/metrics", None),
        ("put", f"{base}/games/{g}/metrics/playtime", {"seconds": 1}),
        ("post", f"{base}/games/{g}/make-main", None),
        ("post", f"{base}/games/{g}/split", {"keep_separate": True}),
        ("get", f"{base}/games/{g}/overview", None),
        ("get", f"{base}/games/{g}/sources", None),
        ("get", f"{base}/games/{g}/tracking", None),
        ("get", f"{base}/games/{g}/achievements", None),
        ("post", f"{base}/games/{g}/manual-achievements", {"title": "x"}),
        ("get", f"{base}/games/{g}/completion", None),
        ("put", f"{base}/games/{g}/completion", {"status": "BEATEN"}),
        ("delete", f"{base}/games/{g}/completion", None),
        ("get", f"{base}/games/{g}/completion/history", None),
        ("get", f"{base}/names/games/{g}", None),
        ("patch", f"{base}/manual-achievements/{w['achievement']}", {"title": "stolen"}),
        ("delete", f"{base}/manual-achievements/{w['achievement']}", None),
        ("put", f"{base}/manual-achievements/{w['achievement']}/earned", {"achieved": True}),
        ("get", f"{base}/manual-achievements/{w['achievement']}/icon", None),
        ("post", f"{base}/relationships", {"kind": "related_to", "from_id": o, "to_id": g}),
        ("put", f"{base}/relationships/{w['relationship']}", {"kind": "dlc_of"}),
        ("put", f"{base}/relationships/{w['relationship']}/display", {"display": "hidden"}),
        ("post", f"{base}/relationships/{w['relationship']}/end", None),
        ("delete", f"{base}/counters/{w['counter']}/adjustment", None),
        ("post", f"{base}/counters/{w['counter']}/group", {"source_counter_id": w["counter"]}),
        ("put", f"{base}/counters/{w['counter']}/members/{w['observation']}/priority", {"priority": 1}),
        ("post", f"{base}/counters/{w['counter']}/members/{w['observation']}/ungroup", None),
        ("post", f"{base}/review/queue/{w['queued']}/merge", None),
        ("post", f"{base}/review/queue/{w['queued']}/keep-separate", None),
        ("post", f"{base}/review/queue/{w['queued']}/relate", {"kind": "port_of", "from_id": g}),
        ("post", f"{base}/review/automatic/{w['auto']}/undo", None),
        ("post", f"{base}/review/automatic/{w['auto']}/keep", None),
        ("post", f"{base}/names/reviews/{w['name_review']}/update", None),
        ("post", f"{base}/names/reviews/{w['name_review']}/keep", None),
    ]  # fmt: skip


def template(path):
    import re

    path = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "{}", path)
    return re.sub(r"\{[a-z_]+\}", "{}", path)


async def world_state(api, w):
    return {
        "games": sorted(g["id"] for g in await live_games(api)),
        "metrics": await metrics(api, w["game"]),
        "tracking": (await api.get(f"{ID}/games/{w['game']}/tracking")).json(),
        "decisions": (await api.get(f"{ID}/decisions")).json(),
        "names": (await api.get(f"{ID}/names/reviews")).json(),
        "queue": (await api.get(f"{ID}/review/queue")).json(),
        "banner": (await api.get(f"{ID}/review/automatic")).json(),
        "library": (await api.get(f"{ID}/library")).json(),
    }


async def test_13_no_identity_or_manual_tracking_route_reaches_another_users_data(clients):  # noqa: F811
    owner, stranger = await clients(), await clients()
    world = await owner_world(owner)
    calls = foreign_calls(world)
    # a new identity route cannot ship without isolation coverage
    from src.main import app
    from tests.route_audit_support import walk_routes

    # The sync/evidence API belongs to this identity audit. Remote launch has
    # a separate browser/receiver boundary and a complete dynamic route-isolation
    # matrix in test_remote_launch.py.
    documented = {
        (method.lower(), path)
        for path, route in walk_routes(app.router)
        if path.startswith("/api/game-identity")
        or route.endpoint.__module__ == "src.api.routes.playnite"
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    covered = {(m, template(p)) for m, p, _ in calls}
    uncovered = (
        {(m, template(p)) for m, p in documented}
        - covered
        - {(m, template(p)) for m, p in OWN_DATA_ONLY}
    )
    assert not uncovered, f"routes without an isolation check: {sorted(uncovered)}"

    for method, path, _ in calls:  # positive control: the owner can read what the stranger cannot
        if method == "get" and "/icon" not in path:
            assert (await owner.api.get(path)).status_code == 200, path
    before = await world_state(owner.api, world)
    rows = await tally(owner.user_id)
    statuses = {}
    for method, path, body in calls:
        sent = await stranger.api.request(method, path, json=body)
        statuses[(method, path)] = sent.status_code
        assert sent.status_code in (404, 409, 422), (method, path, sent.status_code, sent.text)
    assert sum(1 for code in statuses.values() if code == 404) >= len(calls) - 6
    assert await world_state(owner.api, world) == before
    assert await tally(owner.user_id) == rows

    # the stranger's own collections are empty and never leak the owner's rows
    assert (await stranger.api.get(f"{ID}/library")).json()["groups"] == []
    assert (await stranger.api.get(f"{ID}/decisions")).json() == []
    assert (await stranger.api.get(f"{ID}/review/queue")).json()["entries"] == []
    assert (await stranger.api.get(f"{ID}/review/automatic")).json()["merges"] == []
    assert (await stranger.api.get(f"{ID}/names/reviews")).json() == []
    assert (await stranger.api.get("/api/game/list")).json() == []
    ran = await stranger.api.post(f"{ID}/review/run")
    assert ran.json() == {"associated": 0, "suggested": 0, "skipped": 0}
    # unauthenticated callers get nothing at all
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as anonymous:
        for method, path, body in calls[:8]:
            assert (await anonymous.request(method, path, json=body)).status_code in (401, 403)


# --- 14. search finds the umbrella, the exact edition and similarly named separate games ------


async def test_14_search_reaches_umbrella_exact_edition_and_separate_namesakes(clients):  # noqa: F811
    playnite = await clients()
    api = playnite.api
    java = await playnite_game(playnite, str(uuid4()), "Minecraft Java Edition", 40)
    bedrock = await playnite_game(playnite, str(uuid4()), "Minecraft Bedrock Edition", 15)
    dungeons = await manual_game(api, "Minecraft Dungeons")
    legends = await manual_game(api, "Minecraft Legends")
    await merge(api, bedrock, java)
    layout = (await api.get(f"{ID}/library")).json()
    folded = {g["umbrella_id"]: {m["game_id"] for m in g["members"]} for g in layout["groups"]}
    assert folded == {java: {bedrock}}  # one umbrella; the namesakes are not folded into it

    async def found(term):
        return {g["id"] for g in (await api.get("/api/game/list", params={"search": term})).json()}

    assert await found("Minecraft") == {java, bedrock, dungeons, legends}  # umbrella term
    assert await found("Bedrock") == {bedrock}  # the exact edition stays reachable
    assert await found("Minecraft Dungeons") == {dungeons}
    assert dungeons not in folded[java] and legends not in folded[java]
    # a grouped version remains addressable on its own (opens its version, not only the umbrella)
    overview = (await api.get(f"{ID}/games/{bedrock}/overview")).json()
    assert overview["game_id"] == bedrock and overview["aggregate"]["version_count"] == 2
    assert (await api.get(f"/api/game/get/{bedrock}")).json()["title"].endswith("Bedrock Edition")


# --- operations: retries and concurrent sync / merge are safe ---------------------------------


async def test_concurrent_identical_playnite_deliveries_make_one_record(clients):  # noqa: F811
    playnite = await clients()
    api = playnite.api
    guid = str(uuid4())
    game = await import_playnite_entry(playnite, guid, "Minecraft")
    sent = batch(entry(guid, name="Minecraft"))
    replies = await asyncio.gather(
        *(api.post("/api/playnite/observations", json=sent) for _ in range(12))
    )
    assert {r.status_code for r in replies} == {200}
    outcomes = [r.json()["results"][0]["outcome"] for r in replies]
    assert outcomes.count("updated") == 1 and set(outcomes) <= {"updated", "unchanged"}
    assert await tally(playnite.user_id) == (1, 1, 1)
    assert [g["id"] for g in await live_games(api)] == [game]


async def test_concurrent_creates_of_one_playnite_id_return_one_game(clients):  # noqa: F811
    playnite = await clients()
    guid = str(uuid4())
    body = create_body(guid, "Hades")
    replies = await asyncio.gather(
        *(playnite.api.post("/api/game/create", json=body) for _ in range(8))
    )
    assert {r.status_code for r in replies} <= {200, 201}
    assert len({r.json()["id"] for r in replies}) == 1
    assert await tally(playnite.user_id) == (1, 1, 1)


async def test_concurrent_merge_split_and_sync_end_in_one_consistent_state(clients):  # noqa: F811
    playnite = await clients()
    api = playnite.api
    guid = str(uuid4())
    imported = await playnite_game(playnite, guid, "Minecraft", 10)
    steam = str(await steam_game(playnite.user_id, "1672970", "Minecraft", hours=4))
    before = await tally(playnite.user_id)

    async def flip(index):
        if index % 2:
            return await api.post(f"{ID}/unmerge", json={"game_id": steam})
        return await api.post(
            f"{ID}/merge",
            json={"source_id": steam, "target_id": imported, "replace_separate": True},
        )

    async def sync(index):
        sent = entry(guid, name="Minecraft", playtime_seconds=(10 + index) * HOUR)
        return await api.post("/api/playnite/observations", json=batch(sent))

    replies = await asyncio.gather(*(flip(i) for i in range(10)), *(sync(i) for i in range(6)))
    assert all(r.status_code in (200, 409, 422) for r in replies), [
        (r.status_code, r.text) for r in replies if r.status_code >= 500
    ]
    assert not any(r.status_code >= 500 for r in replies)
    assert await tally(playnite.user_id) == before  # nothing created, duplicated or lost
    together = (await api.get(f"{ID}/games/{steam}")).json()["identity_id"] == imported
    total = metrics_of(await metrics(api, imported))["effective"]
    async with SessionLocal() as db:  # the Playnite counter reports its latest accepted reading
        played = await db.scalar(
            select(GameSourceObservation.observed_facts["playtime_seconds"].as_integer()).where(
                GameSourceObservation.user_id == playnite.user_id,
                GameSourceObservation.external_id == guid,
            )
        )
    assert total == (played + 4 * HOUR if together else played)  # the sum of independent counters
    assert len(await live_games(api)) == 2


# --- operations: logs carry provenance and never library content -------------------------------


async def test_logs_name_who_decided_what_and_never_quote_library_content(
    clients,  # noqa: F811
    caplog,
    monkeypatch,
):
    caplog.set_level(logging.INFO)
    playnite = await clients()
    api, user = playnite.api, playnite.user_id
    guid = str(uuid4())
    secret = "Secret Title 7f3a"
    imported = await import_playnite_entry(playnite, guid, secret)
    await api.post("/api/playnite/observations", json=batch(entry(guid, name=secret)))
    by_hand = await manual_game(api, secret)
    merged = await merge(api, by_hand, imported)
    await api.post(f"{ID}/review/run")
    await api.post("/api/playnite/inventory", json={"contract_version": 1, "playnite_ids": [guid]})
    await forget_identity_rows(user)

    async def explode(*_args):
        raise RuntimeError(f"cannot read {secret}")

    monkeypatch.setattr(runner, "STAGES", (("observations", explode),))
    failed = await api.post(f"{ID}/backfill?wait=true")
    assert failed.status_code == 202 and failed.json()["status"] == "failed"

    lines = [r.getMessage() for r in caplog.records if r.name.startswith("src.features")]
    text_ = "\n".join(lines)
    decision_id = merged["decision"]["id"]
    assert any(
        line.startswith("identity decision")
        and decision_id in line
        and "outcome=confirmed_same" in line
        for line in lines
    ), lines
    assert any(line.startswith("identity matching") and "associated=" in line for line in lines)
    assert any(line.startswith("playnite observations") and "updated" in line for line in lines)
    assert any(
        line.startswith("playnite inventory") and "marked_unavailable=0" in line for line in lines
    )
    assert any(
        line.startswith("identity backfill failed") and "error=RuntimeError" in line
        for line in lines
    )
    assert str(user) in text_  # an operator can follow one user's activity
    for private in (secret, guid, "utk_"):
        assert private not in text_, private
