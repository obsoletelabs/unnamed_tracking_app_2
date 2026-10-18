"""Hybrid identity matching and the durable duplicate review queue (#99)."""

from datetime import date

import pytest
from sqlalchemy import update

from src.database.models.game import Game
from src.database.models.game_identity import GameSourceConnection
from src.database.session import SessionLocal
from src.features import game_duplicates
from src.features.game_identity import counters, decisions, matching, review
from src.features.game_identity.matching import Candidate, assess
from src.features.game_ingestion.contract import SourceConnectionRef
from src.features.game_ingestion.service import complete_inventory, disconnect_connection
from tests.test_game_identity_decisions import api, current, root_of, run  # noqa: F401
from tests.test_game_ingestion import ingest, make_game, users  # noqa: F401
from tests.test_game_ingestion_compat import isolated_import_storage  # noqa: F401

STEAM = SourceConnectionRef(source_kind="host", source="steam", source_scope="legacy")
PLAYNITE = SourceConnectionRef(source_kind="host", source="playnite", source_scope="lib")
FACTS = {
    "title": "Hades",
    "release": date(2020, 9, 17),
    "platform": "PC",
    "playtime": 3600,
    "last": 1000,
}


async def record(user, connection, ext, *, observed=True, **over):
    """A library record with every field the strict rule needs, plus its observation."""
    data = {**FACTS, **over}
    game = await make_game(user, data["title"])
    async with SessionLocal() as db:
        await db.execute(
            update(Game)
            .where(Game.id == game)
            .values(
                source=connection.source,
                platform=data["platform"],
                release_date=data["release"],
                playtime_seconds=data["playtime"],
                last_played_at=data["last"],
                provider_ids=data.get("ids", {}),
            )
        )
        await db.commit()
    if observed:
        await ingest(
            user,
            {
                "external_id": ext,
                "title": data["title"],
                **({"edition": data["edition"]} if "edition" in data else {}),
            },
            attach={ext: game},
            connection=connection,
        )
    return game


async def match(user, **kwargs):
    async with SessionLocal() as db:
        result = await review.run_matching(db, user, **kwargs)
        await db.commit()
        return result


async def queue(user):
    async with SessionLocal() as db:
        return (await review.queue(db, user))["entries"]


async def automatic(user):
    async with SessionLocal() as db:
        return await review.automatic_merges(db, user)


# --- the pure rule --------------------------------------------------------------------------


def cand(**over):
    base = {
        "id": 1, "title": "Hades", "source": "steam", "platform": "PC",
        "release_date": date(2020, 9, 17), "playtime_seconds": 60, "last_played_at": 5,
        "trusted": True,
    }  # fmt: skip
    return Candidate(**{**base, **over})


def test_exact_evidence_is_automatic_and_explained():
    verdict = assess(cand(), cand(id=2, source="playnite"))
    assert verdict.level == "auto" and verdict.confidence == matching.AUTO_CONFIDENCE
    assert {"title", "release_date", "platform", "edition", "playtime", "last_played"} <= set(
        verdict.matched
    )


@pytest.mark.parametrize(
    "change",
    [
        {"release_date": None},
        {"platform": None},
        {"playtime_seconds": 0},
        {"playtime_seconds": 61},
        {"last_played_at": None},
        {"last_played_at": 6},
        {"trusted": False},
        {"release_date": date(2020, 9, 18)},
    ],
)
def test_missing_or_unequal_evidence_never_merges(change):
    other = cand(id=2, source="playnite", **change)
    assert assess(cand(), other).level != "auto"
    assert assess(other, cand()).level != "auto"


def test_title_only_is_low_and_near_titles_stay_separate():
    only_title = assess(
        cand(platform=None, release_date=None), cand(id=2, platform=None, release_date=None)
    )
    assert only_title.level == "low"
    assert assess(cand(title="Hades"), cand(id=2, title="Hades II")).level == "low"
    deluxe = assess(cand(), cand(id=2, title="Hades Deluxe Edition"))
    assert deluxe.level == "low"


@pytest.mark.parametrize(
    "change",
    [{"platform": "PS5"}, {"release_date": date(2021, 1, 1)}, {"parent_game_id": 9},
     {"provider_ids": {"igdb": "1"}}, {"observed_edition": "GOTY"}],
)  # fmt: skip
def test_conflicts_override_everything(change):
    left = cand(provider_ids={"igdb": "2"}, observed_edition="Base")
    assert assess(left, cand(id=2, **change)).level == "conflict"


def test_shared_provider_id_is_review_not_auto():
    left = cand(provider_ids={"igdb": "7"}, playtime_seconds=1)
    right = cand(id=2, title="Hades (2020)", provider_ids={"igdb": "7"}, playtime_seconds=9)
    assert assess(left, right).level == "review"


def test_same_source_different_records_conflict():
    assert assess(cand(external_id="1"), cand(id=2, external_id="2")).level == "conflict"


# --- persisted behaviour --------------------------------------------------------------------


async def test_exact_match_associates_and_can_be_undone(users):  # noqa: F811
    user = await users()
    a = await record(user, STEAM, "10")
    b = await record(user, PLAYNITE, "guid-1")
    result = await match(user)
    assert result.associated == 1
    assert await root_of(user, a) == await root_of(user, b)
    banner = (await automatic(user))[0]
    assert banner["confidence"] == matching.AUTO_CONFIDENCE
    async with SessionLocal() as db:
        await review.undo_automatic(db, user, banner["id"])
        await db.commit()
    assert await root_of(user, a) != await root_of(user, b)
    assert await automatic(user) == []
    assert (await match(user)).associated == 0  # undone pairs are never re-merged
    assert await root_of(user, a) != await root_of(user, b)
    async with SessionLocal() as db:
        assert (await game_duplicates.suggestions(db, user))["pairs"] == []


async def test_keep_confirms_and_clears_the_banner(users):  # noqa: F811
    user = await users()
    await record(user, STEAM, "10")
    await record(user, PLAYNITE, "g")
    await match(user)
    banner = (await automatic(user))[0]
    async with SessionLocal() as db:
        await review.keep_automatic(db, user, banner["id"])
        await db.commit()
    assert await automatic(user) == []
    assert [d.outcome for d in await current(user)] == ["confirmed_same"]


async def test_title_only_never_merges_or_queues(users):  # noqa: F811
    user = await users()
    await record(user, STEAM, "10", platform=None, release=None, playtime=0, last=None)
    await record(user, PLAYNITE, "g", platform=None, release=None, playtime=0, last=None)
    result = await match(user)
    # A title alone never makes them the same copy: no merge and no duplicate prompt. A Steam
    # and a Playnite record are offered only as possible copies of one game (#274).
    assert result.associated == 0
    assert [entry["kind"] for entry in await queue(user)] == ["copy"] * result.suggested
    assert result.suggested == 1
    async with SessionLocal() as db:  # the existing highlight still shows it
        assert len((await game_duplicates.suggestions(db, user))["pairs"]) == 1


async def test_medium_confidence_goes_to_a_stable_queue(users):  # noqa: F811
    user = await users()
    a = await record(user, STEAM, "10", playtime=100)
    b = await record(user, PLAYNITE, "g", playtime=200)
    first = await match(user)
    assert (first.associated, first.suggested) == (0, 1)
    entries = await queue(user)
    assert len(entries) == 1 and 0.5 < entries[0]["confidence"] < 0.9
    assert {d["field"] for d in entries[0]["differences"] if not d["same"]} >= {
        "playtime_seconds",
        "source",
    }
    again = await match(user)  # restart / re-import: same entry, no duplicate row
    assert again.suggested == 0
    assert [e["id"] for e in await queue(user)] == [entries[0]["id"]]
    assert len(await current(user, "unresolved")) == 1
    async with SessionLocal() as db:
        await review.resolve_merge(db, user, entries[0]["id"])
        await db.commit()
    assert await root_of(user, a) == await root_of(user, b)
    assert await queue(user) == []


async def test_keep_separate_is_final_and_a_third_record_is_still_compared(users):  # noqa: F811
    user = await users()
    a = await record(user, STEAM, "10", playtime=100)
    b = await record(user, PLAYNITE, "g", playtime=200)
    await match(user)
    entry = (await queue(user))[0]
    async with SessionLocal() as db:
        await review.resolve_separate(db, user, entry["id"])
        await db.commit()
    assert (await match(user)).suggested == 0
    assert await queue(user) == []
    gog = SourceConnectionRef(source_kind="plugin", source="gog", source_scope="acct")
    c = await record(user, gog, "77", playtime=300)
    result = await match(user)
    assert result.suggested == 2  # c against a and against b, not a against b
    pairs = {(e["first"]["id"], e["second"]["id"]) for e in await queue(user)}
    assert all(c in pair for pair in pairs) and len(pairs) == 2
    assert await root_of(user, a) != await root_of(user, b)


async def test_user_decision_outranks_a_perfect_match(users):  # noqa: F811
    user = await users()
    a = await record(user, STEAM, "10")
    b = await record(user, PLAYNITE, "g")
    await run(decisions.separate_identities, user, a, b)
    result = await match(user)
    assert (result.associated, result.suggested) == (0, 0)
    assert await root_of(user, a) != await root_of(user, b)
    async with SessionLocal() as db:  # the legacy list honours the identity decision too
        assert (await game_duplicates.suggestions(db, user))["pairs"] == []


async def test_incomplete_inventory_is_queued_not_merged(users):  # noqa: F811
    user = await users()
    a = await record(user, STEAM, "10")
    b = await record(user, PLAYNITE, "g")
    result = await match(user, inventory_complete=False)
    assert (result.associated, result.suggested) == (0, 1)
    assert await root_of(user, a) != await root_of(user, b)
    # once a complete pull confirms it, the same strict rule may apply
    assert (await match(user)).associated == 1


async def test_unavailable_or_disconnected_sources_are_never_trusted(users):  # noqa: F811
    user = await users()
    a = await record(user, STEAM, "10")
    b = await record(user, PLAYNITE, "g")
    async with SessionLocal() as db:
        await disconnect_connection(db, user, STEAM)
        await db.commit()
    assert (await match(user)).associated == 0
    async with SessionLocal() as db:
        await db.execute(update(GameSourceConnection).values(disconnected_at=None))
        await complete_inventory(db, user, PLAYNITE, set())  # g absent from a complete pull
        await db.commit()
    assert (await match(user)).associated == 0
    assert await root_of(user, a) != await root_of(user, b)


async def test_records_without_observations_are_review_only(users):  # noqa: F811
    user = await users()
    await record(user, STEAM, "10", observed=False)
    await record(user, PLAYNITE, "g", observed=False)
    result = await match(user)
    assert (result.associated, result.suggested) == (0, 1)


async def test_conflicting_evidence_is_neither_merged_nor_queued(users):  # noqa: F811
    user = await users()
    await record(user, STEAM, "10")
    await record(user, PLAYNITE, "g", platform="PS5")
    await record(user, PLAYNITE, "h", release=date(2005, 1, 1))
    result = await match(user)
    entries = await queue(user)
    # Conflicts still rule out "same copy". The PS5 record is offered as another copy of the
    # game; the 2005 record is far enough apart to be a different game sharing the name.
    assert result.associated == 0
    assert [entry["kind"] for entry in entries] == ["copy"]
    assert {entries[0]["first"]["platform"], entries[0]["second"]["platform"]} == {"PC", "PS5"}


async def test_repeated_matching_and_imports_are_idempotent(users):  # noqa: F811
    user = await users()
    await record(user, STEAM, "10")
    await record(user, PLAYNITE, "g")
    await match(user)
    before = await current(user)
    for _ in range(3):
        again = await match(user)
        assert (again.associated, again.suggested) == (0, 0)
    assert [d.id for d in await current(user)] == [d.id for d in before]


async def test_a_lone_edition_difference_is_not_the_same_copy(users):  # noqa: F811
    user = await users()
    await record(user, STEAM, "10")
    await record(user, PLAYNITE, "g", title="Hades Deluxe Edition")
    result = await match(user)
    assert result.associated == 0  # an edition is never merged as the same copy
    assert [entry["kind"] for entry in await queue(user)] == ["copy"]


async def test_users_are_isolated(users):  # noqa: F811
    user, other = await users(), await users()
    await record(user, STEAM, "10")
    await record(other, PLAYNITE, "g")
    assert (await match(user)).associated == 0 and (await match(other)).associated == 0


# --- HTTP -----------------------------------------------------------------------------------


async def test_http_queue_and_banner_flow(users, api):  # noqa: F811
    user, other = await users(), await users()
    a = await record(user, STEAM, "10")
    await record(user, PLAYNITE, "g")
    c = await record(user, STEAM, "11", title="Celeste", playtime=1)
    await record(user, PLAYNITE, "h", title="Celeste", playtime=2)
    async with await api(user) as client:
        ran = await client.post("/api/game-identity/review/run")
        assert ran.json() == {"associated": 1, "suggested": 1, "skipped": ran.json()["skipped"]}
        banner = (await client.get("/api/game-identity/review/automatic")).json()["merges"]
        assert len(banner) == 1 and banner[0]["absorbed"]["title"] == "Hades"
        entry = (await client.get("/api/game-identity/review/queue")).json()["entries"][0]
        assert (
            await client.post(f"/api/game-identity/review/automatic/{banner[0]['id']}/undo")
        ).status_code == 204
        assert (
            await client.post(f"/api/game-identity/review/automatic/{banner[0]['id']}/undo")
        ).status_code in (404, 409)
        bad = await client.post(
            f"/api/game-identity/review/queue/{entry['id']}/merge", json={"target_id": str(a)}
        )
        assert bad.status_code == 422
        assert (
            await client.post(f"/api/game-identity/review/queue/{entry['id']}/keep-separate")
        ).status_code == 204
        assert (await client.get("/api/game-identity/review/queue")).json()["entries"] == []
        assert c  # second pair resolved above
    async with await api(other) as client:
        assert (
            await client.post(f"/api/game-identity/review/queue/{entry['id']}/merge")
        ).status_code == 404


async def _record_with_facts(user, connection, ext):
    game = await record(user, connection, ext, observed=False)
    await ingest(
        user,
        {
            "external_id": ext,
            "title": FACTS["title"],
            "facts": {"playtime_seconds": FACTS["playtime"], "last_played_at": FACTS["last"]},
        },
        attach={ext: game},
        connection=connection,
    )
    return game


async def _playtime(user, game):
    async with SessionLocal() as db:
        view = await counters.reconcile(db, user, game)
        await db.commit()
    return view["metrics"]["playtime"]["effective"]


async def test_automatic_merge_counts_one_copy_once_and_undo_restores_each(users):  # noqa: F811
    user = await users()
    a = await _record_with_facts(user, STEAM, "10")
    b = await _record_with_facts(user, PLAYNITE, "guid-1")
    assert (await match(user)).associated == 1
    # two reports of one copy are the same hour count, not the sum of both
    assert await _playtime(user, a) == 3600
    assert await _playtime(user, b) == 3600
    banner = (await automatic(user))[0]
    async with SessionLocal() as db:
        await review.undo_automatic(db, user, banner["id"])
        await db.commit()
    assert await _playtime(user, a) == 3600
    assert await _playtime(user, b) == 3600


@pytest.mark.parametrize(
    ("first", "second", "shared"),
    [
        ("Minecraft: Java Edition", "Minecraft for Windows", True),
        ("Minecraft: Java Edition", "Minecraft: Bedrock Edition", True),
        ("The Witcher 3: Wild Hunt", "The Witcher 3: Wild Hunt - Game of the Year Edition", True),
        ("The Elder Scrolls V: Skyrim", "The Elder Scrolls V: Skyrim Special Edition", True),
        ("Death Stranding", "Death Stranding Director's Cut", True),
        ("DOOM (2016)", "DOOM", True),
        ("Minecraft", "Minecraft: Story Mode", False),
        ("Hades", "Hades II", False),
        ("Resident Evil 2", "Resident Evil 2 Remake", False),
        ("Final Fantasy VII", "Final Fantasy VII Remake", False),
    ],
)
def test_base_titles_group_editions_but_not_sequels_or_remakes(first, second, shared):
    assert bool(matching.base_title_keys(first) & matching.base_title_keys(second)) is shared


def test_copy_evidence_needs_a_reason_to_be_separate_copies():
    steam = Candidate(id=1, title="Stardew Valley", source="Steam", platform="PC")
    android = Candidate(id=2, title="Stardew Valley", source="manual", platform="Android")
    verdict = matching.assess_copy(steam, android)
    assert verdict is not None and verdict.level == "copy"
    assert set(verdict.conflicts) == {"platform", "store"}
    assert (
        matching.assess_copy(
            steam, Candidate(id=3, title="Stardew Valley", source="Steam", platform="PC")
        )
        is None
    )
    reboot = Candidate(id=4, title="DOOM", platform="PS4", release_date=date(2016, 5, 13))
    original = Candidate(id=5, title="DOOM", platform="PC", release_date=date(1993, 12, 10))
    assert matching.assess_copy(reboot, original) is None
    other = Candidate(id=6, title="Stardew Valley", platform="Switch", provider_ids={"igdb": "1"})
    assert (
        matching.assess_copy(
            other,
            Candidate(id=7, title="Stardew Valley", platform="PC", provider_ids={"igdb": "2"}),
        )
        is None
    )


async def test_accepting_a_copy_suggestion_keeps_the_most_played_copy_as_main(users, api):  # noqa: F811
    user = await users()
    little = await record(user, STEAM, "10", platform="PC", playtime=60)
    most = await record(user, PLAYNITE, "g", platform="Switch", playtime=90_000)
    await match(user)
    async with await api(user) as client:
        entry = (await client.get("/api/game-identity/review/queue")).json()["entries"][0]
        assert entry["kind"] == "copy"
        merged = await client.post(f"/api/game-identity/review/queue/{entry['id']}/merge")
        assert merged.status_code == 204, merged.text
    assert (await root_of(user, little), await root_of(user, most)) == (most, most)


async def test_queue_asks_once_per_pair_of_groups(users):  # noqa: F811
    user = await users()
    steam = await record(user, STEAM, "10", platform="PC")
    epic = await record(user, PLAYNITE, "g", platform="PC", playtime=7)
    switch = await record(user, PLAYNITE, "s", platform="Switch", playtime=9)
    await run(decisions.merge_identities, user, epic, steam)
    await match(user)
    entries = await queue(user)
    assert len(entries) == 1
    assert switch in {entries[0]["first"]["id"], entries[0]["second"]["id"]}
