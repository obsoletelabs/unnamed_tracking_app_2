"""Library layout, umbrella overview and identity management reads (#101)."""

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import update

from src.core.auth import get_current_user
from src.core.preferences import save_preferences, validate_preference
from src.database.models.game import Game, GameStatus
from src.database.session import SessionLocal
from src.features.game_identity import decisions, graph, manual, overview
from src.main import app
from tests.test_game_counters import MANUAL, PLAYNITE, report, run
from tests.test_game_ingestion import ingest, make_game, users  # noqa: F401

HOUR = 3600


async def set_fields(game_id, **values):
    async with SessionLocal() as db:
        await db.execute(update(Game).where(Game.id == game_id).values(**values))
        await db.commit()


async def minecraft(user):
    """Java (root) and Bedrock merged; Dungeons is a different game."""
    java = await make_game(user, "Minecraft Java Edition")
    bedrock = await make_game(user, "Minecraft Bedrock Edition")
    dungeons = await make_game(user, "Minecraft Dungeons")
    for game in (java, bedrock, dungeons):
        await set_fields(game, status=GameStatus.BACKLOG)
    await run(decisions.merge_identities, user, bedrock, java)
    return java, bedrock, dungeons


async def layout(user):
    return await run(overview.library_layout, user)


async def test_merged_versions_share_one_umbrella_and_distinct_games_stay_apart(users):  # noqa: F811
    user = await users()
    java, bedrock, dungeons = await minecraft(user)
    found = await layout(user)
    assert found["mode"] == "grouped"
    assert [g["umbrella_id"] for g in found["groups"]] == [java]
    assert found["groups"][0]["members"] == [
        {
            "game_id": bedrock,
            "display": "grouped",
            "relationship_id": found["groups"][0]["members"][0]["relationship_id"],
        }
    ]
    assert dungeons not in {m["game_id"] for g in found["groups"] for m in g["members"]}


async def test_link_and_hidden_edges_change_how_a_version_is_shown(users):  # noqa: F811
    user = await users()
    java, bedrock, _ = await minecraft(user)
    edge = (await layout(user))["groups"][0]["members"][0]["relationship_id"]
    await run(overview.set_relationship_display, user, edge, "link")
    assert (await layout(user))["groups"][0]["members"][0]["display"] == "separate"
    await run(overview.set_relationship_display, user, edge, "hidden")
    assert (await layout(user))["groups"][0]["members"][0]["display"] == "hidden"
    await run(overview.set_relationship_display, user, edge, "default")
    assert (await layout(user))["groups"][0]["members"][0]["display"] == "grouped"


async def test_trashed_root_hands_the_umbrella_to_a_live_version(users):  # noqa: F811
    user = await users()
    java, bedrock, _ = await minecraft(user)
    await set_fields(java, deleted_at=1)
    found = await layout(user)
    assert found["groups"] == []  # one live version is not a group


async def test_relationship_rules_resolve_default_edges(users):  # noqa: F811
    user = await users()
    java, _, dungeons = await minecraft(user)
    async with SessionLocal() as db:
        await save_preferences(db, user, {"library_relationship_display": {"related_to": "nest"}})
    await run(graph.add_relationship, user, "related_to", dungeons, java)
    await run(graph.add_relationship, user, "dlc_of", dungeons, java, display="hidden")
    related = {r["kind"]: r["display"] for r in (await layout(user))["related"]}
    assert related == {"related_to": "nest", "dlc_of": "hidden"}


def test_display_preferences_are_validated():
    assert validate_preference("library_version_display", "separate") == "separate"
    with pytest.raises(ValueError):
        validate_preference("library_version_display", "stacked")
    assert validate_preference("library_relationship_display", {"dlc_of": "nest"}) == {
        "dlc_of": "nest"
    }
    for bad in ({"same_game": "nest"}, {"dlc_of": "default"}, {"nope": "link"}, []):
        with pytest.raises(ValueError):
            validate_preference("library_relationship_display", bad)


async def test_overview_separates_overall_from_per_version_values(users):  # noqa: F811
    user = await users()
    java, bedrock, _ = await minecraft(user)
    await report(user, java, PLAYNITE, "p1", 200, 10)
    await set_fields(java, platform="PC", status=GameStatus.BEATEN, last_played_at=500)
    # a record with no source observation falls back to its own values, never to zero
    await set_fields(bedrock, platform="Xbox", playtime_seconds=50 * HOUR, last_played_at=900)
    await run(manual.set_completion, user, java, GameStatus.BEATEN)
    body = await run(overview.game_overview, user, bedrock)  # same answer from any member
    assert body == await run(overview.game_overview, user, java) | {"game_id": bedrock}
    aggregate = body["aggregate"]
    assert aggregate["playtime_seconds"] == 250 * HOUR
    assert aggregate["last_played_at"] == 900
    assert aggregate["version_count"] == 2 and aggregate["owned_count"] == 2
    assert aggregate["owned_platforms"] == ["PC", "Xbox"]
    assert aggregate["completion"]["partial"] is True
    assert aggregate["completion"]["summary"] == "Completed on 1 of 2 tracked versions"
    by_id = {v["game_id"]: v for v in body["versions"]}
    assert (
        by_id[java]["playtime_seconds"] == 200 * HOUR
        and by_id[java]["playtime_basis"] == "reconciled"
    )
    assert (
        by_id[bedrock]["playtime_seconds"] == 50 * HOUR
        and by_id[bedrock]["playtime_basis"] == "record"
    )
    assert by_id[java]["is_primary"] and not by_id[bedrock]["is_primary"]
    assert by_id[java]["sources"] == ["playnite"] and by_id[bedrock]["sources"] == []
    assert [v["game_id"] for v in body["versions"]][0] == java


async def test_manual_and_playnite_values_for_the_same_counter_are_not_summed(users):  # noqa: F811
    user = await users()
    java = await make_game(user, "Minecraft")
    await report(user, java, PLAYNITE, "p1", 200, 10)
    await report(user, java, MANUAL, "m1", 195, 20)
    body = await run(overview.game_overview, user, java)
    assert body["aggregate"]["playtime_seconds"] == 395 * HOUR  # independent counters add up
    assert body["versions"][0]["playtime_seconds"] == 395 * HOUR


async def test_a_plain_playnite_game_without_new_metadata_still_has_an_overview(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Hades")
    await set_fields(game, playtime_seconds=3 * HOUR, last_played_at=77, status=GameStatus.PLAYING)
    body = await run(overview.game_overview, user, game)
    assert body["aggregate"]["playtime_seconds"] == 3 * HOUR
    assert body["aggregate"]["last_played_at"] == 77
    assert body["aggregate"]["version_count"] == 1
    assert body["aggregate"]["completion"]["partial"] is False
    assert body["aggregate"]["achievements"]["total"] == 0


async def test_wishlist_versions_are_not_counted_as_owned(users):  # noqa: F811
    user = await users()
    java, bedrock, _ = await minecraft(user)
    await set_fields(bedrock, status=GameStatus.WISHLIST, platform="Xbox")
    body = await run(overview.game_overview, user, java)
    assert body["aggregate"]["owned_count"] == 1
    assert "Xbox" not in body["aggregate"]["owned_platforms"]


async def test_sources_explain_the_identity(users):  # noqa: F811
    user = await users()
    java, bedrock, dungeons = await minecraft(user)
    await report(user, java, PLAYNITE, "p1", 200, 10)
    await run(decisions.separate_identities, user, java, dungeons)
    body = await run(overview.identity_sources, user, bedrock)
    assert body["umbrella_id"] == java
    assert {v["game_id"] for v in body["versions"]} == {java, bedrock}
    [observation] = body["observations"]
    assert observation["source"] == "playnite" and observation["game_id"] == java
    assert observation["facts"]["playtime_seconds"] == 200 * HOUR
    assert [r["kind"] for r in body["relationships"]] == ["same_game"]
    outcomes = {d["outcome"] for d in body["decisions"]}
    assert {"confirmed_same", "confirmed_separate"} <= outcomes
    assert body["titles"][str(dungeons)] == "Minecraft Dungeons"
    assert body["counters"]["playtime"][0]["raw"] == 200 * HOUR


async def test_changing_a_relationship_keeps_the_old_edge_in_history(users):  # noqa: F811
    user = await users()
    java, _, dungeons = await minecraft(user)
    edge = await run(graph.add_relationship, user, "related_to", dungeons, java)
    changed = await run(
        overview.change_relationship, user, edge.id, kind="remake_of", label="", display="nest"
    )
    assert changed.id != edge.id and changed.kind == "remake_of" and changed.display == "nest"
    body = await run(overview.identity_sources, user, dungeons)
    assert {r["kind"] for r in body["relationships"]} == {"related_to", "remake_of"}
    body = await run(overview.identity_sources, user, java)
    kinds = {(r["kind"], r["ended_at"] is not None) for r in body["relationships"]}
    assert ("related_to", True) in kinds and ("remake_of", False) in kinds
    merge_edge = next(r for r in body["relationships"] if r["kind"] == "same_game")
    with pytest.raises(graph.IdentityInvalid):
        await run(overview.change_relationship, user, merge_edge["id"], kind="dlc_of")
    with pytest.raises(graph.IdentityInvalid):  # a custom relationship needs a label
        await run(overview.change_relationship, user, changed.id, kind="custom", label="")


async def test_http_routes_and_isolation(users):  # noqa: F811
    user, other = await users(), await users()
    java, bedrock, _ = await minecraft(user)
    await report(user, java, PLAYNITE, "p1", 10, 10)
    holder = {"id": user}
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=holder["id"])
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            library = (await client.get("/api/game-identity/library")).json()
            assert library["groups"][0]["umbrella_id"] == str(java)
            edge = library["groups"][0]["members"][0]["relationship_id"]
            got = await client.put(
                f"/api/game-identity/relationships/{edge}/display", json={"display": "link"}
            )
            assert got.status_code == 200 and got.json()["display"] == "link"
            assert (
                await client.put(
                    f"/api/game-identity/relationships/{edge}/display", json={"display": "bogus"}
                )
            ).status_code == 422
            body = (await client.get(f"/api/game-identity/games/{bedrock}/overview")).json()
            assert body["aggregate"]["version_count"] == 2
            assert (
                await client.get(f"/api/game-identity/games/{bedrock}/sources")
            ).status_code == 200
            holder["id"] = other
            assert (
                await client.get(f"/api/game-identity/games/{java}/overview")
            ).status_code == 404
            assert (await client.get(f"/api/game-identity/games/{java}/sources")).status_code == 404
            assert (
                await client.put(
                    f"/api/game-identity/relationships/{edge}/display", json={"display": "nest"}
                )
            ).status_code == 404
            assert (await client.get("/api/game-identity/library")).json()["groups"] == []
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_first_umbrella_wide_achievement_places_the_merged_group(users):  # noqa: F811
    user = await users()
    java, bedrock, _ = await minecraft(user)
    first = await run(
        manual.create_achievement, user, bedrock, {"title": "Beat the dragon"}, umbrella=True
    )
    again = await run(
        manual.create_achievement, user, java, {"title": "Build a beacon"}, umbrella=True
    )
    assert first.umbrella_id == again.umbrella_id is not None
    async with SessionLocal() as db:
        from sqlalchemy import select

        from src.database.models.game_identity import GameIdentity

        rows = (await db.scalars(select(GameIdentity).where(GameIdentity.user_id == user))).all()
    assert {r.game_id for r in rows} == {java, bedrock}
    assert [r.game_id for r in rows if r.is_primary] == [java]
    body = await run(overview.game_overview, user, bedrock)
    assert body["aggregate"]["achievements"]["total"] == 2
