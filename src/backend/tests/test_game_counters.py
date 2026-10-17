"""Per-metric counters, reconciliation and persistent adjustments (#96)."""

import random
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from src.core.auth import get_current_user
from src.database.models.game_identity_tracking import (
    GameMetricAdjustment,
    GameMetricCounter,
    GameMetricCounterMember,
)
from src.database.session import SessionLocal
from src.features.game_identity import counters, decisions, graph, policy
from src.features.game_identity.graph import IdentityConflict, IdentityInvalid
from src.features.game_ingestion.contract import SourceConnectionRef
from src.main import app
from tests.test_game_ingestion import STEAM, ingest, make_game, users  # noqa: F401

HOUR = 3600
PLAYNITE = SourceConnectionRef(source_kind="host", source="playnite", source_scope="legacy")
MANUAL = SourceConnectionRef(source_kind="manual", source="manual", source_scope="local")
XBOX = SourceConnectionRef(source_kind="plugin", source="xbox", source_scope="acct")


async def run(fn, *args, **kwargs):
    async with SessionLocal() as db:
        result = await fn(db, *args, **kwargs)
        await db.commit()
        return result


async def view(user, game):
    return await run(counters.reconcile, user, game)


def playtime(v):
    return v["metrics"]["playtime"]


async def report(user, game, connection, ext, hours, now, **extra):
    """One source reports playtime for an observation linked to `game`."""
    facts = {"playtime_seconds": hours * HOUR, **extra}
    return await ingest(
        user,
        {"external_id": ext, "title": "Minecraft", "facts": facts},
        attach={ext: game},
        connection=connection,
        now=now,
    )


async def counter_of(user, game, ext_source):
    v = await view(user, game)
    return next(c for c in playtime(v)["counters"] if c["selected_source"] == ext_source)[
        "counter_id"
    ]


# --- pure policy ----------------------------------------------------------------------------


def reading(value, observed_at, priority=100, oid=None):
    return policy.Reading(oid or uuid4(), "s", value, observed_at, priority)


def test_priority_then_freshness_then_id_is_deterministic():
    a, b, c = reading(200, 10, 100), reading(195, 50, 100), reading(150, 99, 0)
    assert policy.pick_preferred([a, b]) is b  # fresher report of this metric wins
    assert policy.pick_preferred([a, b, c]) is c  # explicit priority beats freshness
    assert policy.pick_preferred([reading(None, 99), a]) is a  # unknown never wins


def test_selection_ignores_input_order_and_never_sums():
    rng = random.Random(96)
    for _ in range(300):
        rows = [
            reading(
                rng.choice([None, *range(0, 500)]),
                rng.choice([None, *range(0, 20)]),
                rng.choice([0, 100, 100, 200]),
            )
            for _ in range(rng.randint(0, 6))
        ]
        shuffled = rows[:]
        rng.shuffle(shuffled)
        winner = policy.pick_preferred(rows)
        assert winner is policy.pick_preferred(shuffled)
        if winner is not None:
            assert winner in rows and winner.value in {
                r.value for r in rows
            }  # one reading, not a sum


def test_adjustment_math():
    assert policy.apply_playtime_adjustment(200, "offset", -10, None) == 190
    assert policy.apply_playtime_adjustment(210, "offset", -10, None) == 200
    assert policy.apply_playtime_adjustment(5, "offset", -10, None) == 0  # clamped
    assert (
        policy.apply_playtime_adjustment(None, "offset", -10, None) is None
    )  # unknown stays unknown
    assert policy.apply_playtime_adjustment(None, "override", None, 42) == 42
    assert policy.apply_playtime_adjustment(7, None, None, None) == 7
    assert policy.offset_for(190, 200) == -10


def test_achievement_union_counts_once():
    assert policy.achievement_union(
        [("First Blood", True), (" first  blood", False), ("Win", False)]
    ) == (2, 1)
    assert policy.combine_any_true([None, False]) is False
    assert policy.combine_any_true([None, None]) is None
    assert policy.combine_any_true([False, True]) is True


# --- counters across observations -----------------------------------------------------------


async def test_independent_counters_sum_and_same_counter_is_not_summed(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Minecraft")
    await report(user, game, STEAM, "s1", 100, 10)
    await report(user, game, PLAYNITE, "p1", 200, 10)
    v = await view(user, game)
    assert playtime(v)["effective"] == 300 * HOUR  # independent by default: summed
    c_pl, c_man = await counter_of(user, game, "playnite"), None
    await report(user, game, MANUAL, "m1", 195, 20)
    c_man = await counter_of(user, game, "manual")
    await run(counters.group_counters, user, c_pl, c_man)
    v = await view(user, game)
    assert playtime(v)["effective"] == (100 + 195) * HOUR  # manual is fresher: 195, not 395
    group = next(c for c in playtime(v)["counters"] if len(c["members"]) == 2)
    assert group["raw"] == 195 * HOUR and group["selected_source"] == "manual"
    assert [x["reason"] for x in group["conflicts"]] == ["older report of this metric"]
    assert group["conflicts"][0]["value"] == 200 * HOUR and group["conflicts"][0]["differs"]
    assert await view(user, game) == v  # reading twice changes nothing


async def test_name_timestamps_do_not_change_playtime_precedence(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Minecraft")
    await report(user, game, PLAYNITE, "p1", 200, 10)
    await report(user, game, MANUAL, "m1", 195, 20)
    await run(
        counters.group_counters,
        user,
        await counter_of(user, game, "playnite"),
        await counter_of(user, game, "manual"),
    )
    # Playnite renames the game much later without reporting playtime again
    await ingest(
        user, {"external_id": "p1", "title": "Minecraft: Java"}, connection=PLAYNITE, now=500
    )
    v = await view(user, game)
    assert playtime(v)["effective"] == 195 * HOUR
    # priority is explicit and wins over freshness
    cid = await counter_of(user, game, "manual")
    pl = next(m for m in playtime(v)["counters"][0]["members"] if m["source"] == "playnite")
    await run(counters.set_priority, user, cid, pl["observation_id"], 0)
    assert playtime(await view(user, game))["effective"] == 200 * HOUR


async def test_missing_or_partial_reports_never_zero_a_value(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Minecraft")
    await report(user, game, PLAYNITE, "p1", 200, 10)
    await ingest(user, {"external_id": "p1", "title": "Renamed"}, connection=PLAYNITE, now=30)
    await ingest(
        user, {"external_id": "p1", "facts": {"favorite": True}}, connection=PLAYNITE, now=40
    )
    assert playtime(await view(user, game))["effective"] == 200 * HOUR
    # a counter that never had playtime reports unknown, not zero
    other = await make_game(user, "Other")
    await ingest(
        user, {"external_id": "z", "title": "Z"}, attach={"z": other}, connection=XBOX, now=1
    )
    assert playtime(await view(user, other))["effective"] is None


async def test_adjustment_survives_source_updates_priority_and_retry(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Minecraft")
    await report(user, game, PLAYNITE, "p1", 200, 10)
    assert await run(counters.adjust_total, user, game, 190 * HOUR, reason="idle time")
    assert not await run(counters.adjust_total, user, game, 190 * HOUR)  # retry is a no-op
    v = await view(user, game)
    adj = playtime(v)["counters"][0]["adjustment"]
    assert (adj["kind"], adj["offset"]) == ("offset", -10 * HOUR)
    assert playtime(v)["effective"] == 190 * HOUR
    await report(user, game, PLAYNITE, "p1", 210, 99)  # source sync
    assert playtime(await view(user, game))["effective"] == 200 * HOUR
    await report(user, game, PLAYNITE, "p1", 210, 100)  # retry of the same report
    assert playtime(await view(user, game))["effective"] == 200 * HOUR
    async with SessionLocal() as db:
        rows = await db.scalar(select(func.count()).select_from(GameMetricAdjustment))
        current = await db.scalar(
            select(func.count())
            .select_from(GameMetricAdjustment)
            .where(
                GameMetricAdjustment.user_id == user, GameMetricAdjustment.superseded_at.is_(None)
            )
        )
    assert current == 1 and rows >= 1
    # a different source takes over: the offset is kept and the rebase is recorded
    await report(user, game, MANUAL, "m1", 195, 200)
    cp, cm = await counter_of(user, game, "playnite"), await counter_of(user, game, "manual")
    await run(counters.group_counters, user, cp, cm)
    v = await view(user, game)
    adj = playtime(v)["counters"][0]["adjustment"]
    assert playtime(v)["effective"] == 185 * HOUR and adj["rebased"]
    assert adj["current_base_observation_id"] != adj["base_observation_id"] or adj["rebased"]
    await run(counters.clear_adjustment, user, cp)
    assert playtime(await view(user, game))["effective"] == 195 * HOUR


async def test_adjusting_to_the_base_clears_and_bounds_hold(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Minecraft")
    await report(user, game, PLAYNITE, "p1", 10, 10)
    await run(counters.adjust_total, user, game, 5 * HOUR)
    await run(counters.adjust_total, user, game, 10 * HOUR)
    assert playtime(await view(user, game))["counters"][0]["adjustment"] is None
    with pytest.raises(IdentityInvalid):
        await run(counters.adjust_total, user, game, -1)
    empty = await make_game(user, "Empty")
    with pytest.raises(IdentityInvalid):
        await run(counters.adjust_total, user, empty, 5)


async def test_adjustment_survives_merge_unmerge_and_restart(users):  # noqa: F811
    user = await users()
    a, b = await make_game(user, "Minecraft Java"), await make_game(user, "Minecraft Bedrock")
    await report(user, a, PLAYNITE, "p1", 200, 10)
    await report(user, b, XBOX, "x1", 70, 10)
    await run(counters.adjust_total, user, a, 190 * HOUR)
    assert playtime(await view(user, a))["effective"] == 190 * HOUR
    await run(decisions.merge_identities, user, a, b)
    merged = await view(user, a)
    assert playtime(merged)["effective"] == 260 * HOUR  # 190 (adjusted) + 70
    assert await view(user, b) == {**merged, "game_id": b}
    await run(counters.adjust_total, user, b, 250 * HOUR)  # edit the merged total
    assert playtime(await view(user, a))["effective"] == 250 * HOUR
    await run(decisions.unmerge_identity, user, a)
    assert (
        playtime(await view(user, a))["effective"] + playtime(await view(user, b))["effective"]
        == 250 * HOUR
    )
    await run(decisions.merge_identities, user, a, b, replace_separate=True)
    await run(decisions.unmerge_identity, user, a)  # restart: everything is in the database
    after = (playtime(await view(user, a))["effective"], playtime(await view(user, b))["effective"])
    assert sum(after) == 250 * HOUR


async def test_moving_an_observation_carries_its_adjustment(users):  # noqa: F811
    user = await users()
    a, b = await make_game(user, "A"), await make_game(user, "B")
    await report(user, a, PLAYNITE, "p1", 100, 10)
    await run(counters.adjust_total, user, a, 90 * HOUR)
    obs = playtime(await view(user, a))["counters"][0]["selected_observation_id"]
    await run(decisions.move_observation, user, obs, b)
    assert playtime(await view(user, a))["effective"] is None
    moved = playtime(await view(user, b))
    assert moved["effective"] == 90 * HOUR and moved["counters"][0]["adjustment"]["carried"]
    await report(user, b, PLAYNITE, "p1", 110, 50)
    assert playtime(await view(user, b))["effective"] == 100 * HOUR
    async with SessionLocal() as db:  # nothing deleted: the old counter is archived
        archived = await db.scalar(
            select(func.count())
            .select_from(GameMetricCounter)
            .where(GameMetricCounter.user_id == user, GameMetricCounter.archived_at.is_not(None))
        )
    assert archived == 1


async def test_ungroup_restores_prior_counters_and_adjustments(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Minecraft")
    await report(user, game, PLAYNITE, "p1", 200, 10)
    await report(user, game, MANUAL, "m1", 195, 20)
    cp, cm = await counter_of(user, game, "playnite"), await counter_of(user, game, "manual")
    await run(counters.adjust_counter, user, cp, 190 * HOUR)
    before = await view(user, game)
    await run(counters.group_counters, user, cm, cp)
    grouped = await view(user, game)
    assert playtime(grouped)["effective"] == 185 * HOUR  # the adjustment follows the group
    pl_obs = next(
        m["observation_id"]
        for m in playtime(grouped)["counters"][0]["members"]
        if m["source"] == "playnite"
    )
    await run(counters.ungroup_observation, user, cm, pl_obs)
    assert await view(user, game) == before
    async with SessionLocal() as db:
        members = await db.scalar(
            select(func.count())
            .select_from(GameMetricCounterMember)
            .where(
                GameMetricCounterMember.user_id == user,
                GameMetricCounterMember.removed_at.is_(None),
            )
        )
    assert members == 2
    with pytest.raises(IdentityConflict):  # counters of unmerged games cannot be grouped
        other = await make_game(user, "Other")
        await report(user, other, XBOX, "o1", 1, 1)
        await run(counters.group_counters, user, cm, await counter_of(user, other, "xbox"))


async def test_randomised_operations_never_double_count(users):  # noqa: F811
    """Whatever mix of syncs, adjustments, merges and splits happens, the total is the
    sum over independent counters of one reading each (plus the stored offsets)."""
    rng = random.Random(7)
    user = await users()
    games = [await make_game(user, f"G{i}") for i in range(3)]
    sources = [PLAYNITE, MANUAL, XBOX, STEAM]
    values: dict[tuple[int, int], int] = {}
    for step in range(40):
        action = rng.choice(["report", "report", "merge", "split", "adjust"])
        gi = rng.randrange(3)
        if action == "report":
            si = rng.randrange(4)
            hours = rng.randrange(1, 300)
            await report(user, games[gi], sources[si], f"e{gi}{si}", hours, step + 1)
            values[(gi, si)] = hours
        elif action == "merge":
            gj = (gi + 1) % 3
            try:
                await run(
                    decisions.merge_identities, user, games[gi], games[gj], replace_separate=True
                )
            except IdentityConflict:
                pass
        elif action == "split":
            try:
                await run(decisions.unmerge_identity, user, games[gi])
            except IdentityConflict:
                pass
        elif values:
            try:
                await run(counters.adjust_total, user, games[gi], rng.randrange(0, 800) * HOUR)
            except IdentityInvalid:
                pass
        for g in games:
            v = await view(user, g)
            members = [m for c in playtime(v)["counters"] for m in c["members"]]
            ids = [m["observation_id"] for m in members]
            assert len(ids) == len(set(ids))  # an observation feeds one counter only
            for c in playtime(v)["counters"]:
                adj = c["adjustment"]
                expected = (
                    c["raw"]
                    if adj is None
                    else (
                        adj["override"]
                        if adj["kind"] == "override"
                        else max(0, c["raw"] + adj["offset"])
                    )
                )
                assert c["effective"] == expected  # exactly one reading plus its own offset
            effective = [c["effective"] for c in playtime(v)["counters"]]
            assert playtime(v)["effective"] == (sum(effective) if effective else None)
    for g in games:  # reading is stable across a fresh session
        assert await view(user, g) == await view(user, g)


# --- other metrics --------------------------------------------------------------------------


async def test_other_metric_policies(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Minecraft")
    await report(
        user,
        game,
        STEAM,
        "s",
        1,
        10,
        last_played_at=1000,
        owned=True,
        installed=True,
        status="completed",
        achievements_total=50,
        achievements_unlocked=20,
    )
    await report(
        user,
        game,
        XBOX,
        "x",
        1,
        10,
        last_played_at=2000,
        owned=False,
        installed=False,
        status="playing",
        achievements_total=50,
        achievements_unlocked=30,
    )
    m = (await view(user, game))["metrics"]
    assert m["last_played"]["effective"] == 2000
    assert m["ownership"]["effective"] is True
    assert m["completion"]["summary"] == "Completed on 1 of 2"
    assert {(s["source"], s["installed"]) for s in m["installed"]["per_source"]} == {
        ("steam", True),
        ("xbox", False),
    }
    assert (
        m["achievements"]["unlocked"] == 30 and m["achievements"]["total"] == 50
    )  # never 50 / 100
    assert not m["achievements"]["exact"]
    assert (await view(user, game))["rating"] is None


async def test_users_are_isolated(users):  # noqa: F811
    one, two = await users(), await users()
    game = await make_game(one, "Mine")
    with pytest.raises(LookupError):
        await view(two, game)
    await report(one, game, PLAYNITE, "p1", 5, 1)
    cid = await counter_of(one, game, "playnite")
    with pytest.raises(LookupError):
        await run(counters.adjust_counter, two, cid, 1)
    assert graph  # imported for the shared fixtures


async def test_http_metrics_adjustment_and_isolation(users):  # noqa: F811
    user, other = await users(), await users()
    game = await make_game(user, "Minecraft")
    await report(user, game, PLAYNITE, "p1", 200, 10)
    await report(user, game, MANUAL, "m1", 195, 20)
    holder = {"id": user}
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=holder["id"])
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            body = (await client.get(f"/api/game-identity/games/{game}/metrics")).json()
            assert body["metrics"]["playtime"]["effective"] == 395 * HOUR  # independent counters
            cid = body["metrics"]["playtime"]["counters"][0]["counter_id"]
            other_cid = body["metrics"]["playtime"]["counters"][1]["counter_id"]
            grouped = await client.post(
                f"/api/game-identity/counters/{cid}/group", json={"source_counter_id": other_cid}
            )
            assert grouped.status_code == 200
            edited = await client.put(
                f"/api/game-identity/games/{game}/metrics/playtime", json={"seconds": 190 * HOUR}
            )
            assert edited.json()["metrics"]["playtime"]["effective"] == 190 * HOUR
            counter = edited.json()["metrics"]["playtime"]["counters"][0]
            assert counter["adjustment"]["offset"] == -5 * HOUR and len(counter["members"]) == 2
            assert (
                await client.put(
                    f"/api/game-identity/games/{game}/metrics/playtime", json={"seconds": -1}
                )
            ).status_code == 422
            holder["id"] = other
            assert (await client.get(f"/api/game-identity/games/{game}/metrics")).status_code == 404
            assert (
                await client.delete(f"/api/game-identity/counters/{cid}/adjustment")
            ).status_code == 404
            holder["id"] = user
            assert (
                await client.delete(f"/api/game-identity/counters/{cid}/adjustment")
            ).status_code == 204
    finally:
        app.dependency_overrides.pop(get_current_user, None)
