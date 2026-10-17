"""Reversible merges, splits and durable identity decisions (#95)."""

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from src.core.auth import get_current_user
from src.database.models.game_identity import (
    GameRelationship,
    GameSourceObservation,
    GameSourceObservationLink,
    GameSourceObservationRevision,
)
from src.database.session import SessionLocal
from src.features import game_duplicates
from src.features.game_identity import decisions, graph
from src.main import app
from tests.test_game_ingestion import ingest, make_game, observation, users  # noqa: F401
from tests.test_game_ingestion_compat import isolated_import_storage  # noqa: F401
from tests.test_playnite_contract import clients, storage  # noqa: F401

AUTO = {"rule": "same-title", "rule_version": "1", "confidence": 0.9, "reason": "Same title"}


async def run(fn, *args, **kwargs):
    """One service call in its own transaction, like one request."""
    async with SessionLocal() as db:
        result = await fn(db, *args, **kwargs)
        await db.commit()
        return result


async def root_of(user, game):
    async with SessionLocal() as db:
        return await graph.resolve_identity(db, user, game)


async def current(user, outcome=None):
    async with SessionLocal() as db:
        return await decisions.list_decisions(db, user, outcome=outcome)


async def active_edges(user):
    async with SessionLocal() as db:
        return await graph.active_edges(db, user, "same_game")


async def three(users):  # noqa: F811
    user = await users()
    return user, [await make_game(user, t) for t in ("A", "B", "C")]


# --- merge / unmerge ------------------------------------------------------------------------


async def test_merge_is_a_relationship_and_unmerge_restores_independent_views(users):  # noqa: F811
    user, (a, b, _c) = await three(users)
    await ingest(user, {"external_id": "1", "title": "A"}, attach={"1": a})
    await ingest(user, {"external_id": "2", "title": "B"}, attach={"2": b}, now=5)
    merged = await run(decisions.merge_identities, user, a, b, decisions.Actor(reason="Same"))
    assert merged.merged and merged.relationship.kind == "same_game"
    assert (await root_of(user, a), await root_of(user, b)) == (b, b)
    again = await run(decisions.merge_identities, user, a, b)
    assert not again.merged and len(await active_edges(user)) == 1

    split = await run(decisions.unmerge_identity, user, a)
    assert split.detached == {a}
    assert (await root_of(user, a), await root_of(user, b)) == (a, b)
    assert await active_edges(user) == []
    async with SessionLocal() as db:  # nothing was lost: both observations still linked
        links = await db.scalars(
            select(GameSourceObservationLink.game_id).where(
                GameSourceObservationLink.user_id == user,
                GameSourceObservationLink.unlinked_at.is_(None),
            )
        )
        assert sorted(links) == sorted([a, b])
        ended = await db.scalar(
            select(func.count())
            .select_from(GameRelationship)
            .where(GameRelationship.user_id == user, GameRelationship.ended_at.is_not(None))
        )
        assert ended == 1  # history kept, not deleted


async def test_decision_records_provenance_prior_and_resulting_state(users):  # noqa: F811
    user, (a, b, _c) = await three(users)
    await run(decisions.merge_identities, user, a, b, decisions.Actor(reason="Same game"), now=10)
    await run(decisions.unmerge_identity, user, a, now=20)
    history = [
        d
        for d in await run(decisions.list_decisions, user, include_superseded=True)
        if d.subject_kind == "identity_pair"
    ]
    by_outcome = {d.outcome: d for d in history}
    same, separate = by_outcome["confirmed_same"], by_outcome["confirmed_separate"]
    assert (same.actor_kind, same.reason, same.created_at, same.superseded_at) == (
        "user",
        "Same game",
        10,
        20,
    )
    assert same.resulting_state["into"] == str(b)
    assert separate.prior_state["decision_id"] == str(same.id)
    assert separate.superseded_at is None


async def test_merge_sync_unmerge_playnite_stays_idempotent(clients):  # noqa: F811
    playnite = await clients()
    guids = [str(uuid4()), str(uuid4())]
    ids = []
    for index, guid in enumerate(guids):
        body = {"title": f"Hades {index}", "folder_location": f"H{index}", "playnite_guid": guid}
        response = await playnite.api.post("/api/game/create", json=body)
        assert response.status_code == 201
        ids.append(UUID(response.json()["id"]))

    async def snapshot():
        async with SessionLocal() as db:
            return [
                await db.scalar(
                    select(func.count()).select_from(model).where(model.user_id == playnite.user_id)
                )
                for model in (
                    GameSourceObservation,
                    GameSourceObservationRevision,
                    GameSourceObservationLink,
                )
            ]

    async def sync():
        for index, guid in enumerate(guids):
            body = {
                "title": f"Hades {index}",
                "folder_location": f"H{index}",
                "playnite_guid": guid,
            }
            response = await playnite.api.post("/api/game/create", json=body)
            assert (response.status_code, response.json()["id"]) == (200, str(ids[index]))

    baseline = await snapshot()
    await run(decisions.merge_identities, playnite.user_id, ids[0], ids[1])
    await sync()
    await sync()
    assert await snapshot() == baseline
    await run(decisions.unmerge_identity, playnite.user_id, ids[0])
    await sync()
    assert await snapshot() == baseline
    assert [await root_of(playnite.user_id, g) for g in ids] == ids


# --- rejected pairs and precedence ----------------------------------------------------------


async def test_a_rejected_pair_stays_rejected_but_a_third_record_is_compared(users):  # noqa: F811
    user, (a, b, c) = await three(users)
    await run(decisions.merge_identities, user, a, b)
    await run(decisions.unmerge_identity, user, a)  # keep_separate by default
    first = await run(decisions.associate_automatically, user, a, b, **AUTO)
    assert first.outcome == "blocked" and await active_edges(user) == []
    # "restart": a fresh session sees the same persisted rejection, in either order
    again = await run(decisions.associate_automatically, user, b, a, **AUTO)
    assert again.outcome == "blocked"
    assert await run(decisions.is_pair_rejected, user, a, b)
    # a new record is compared on its own; the rejection is not silently reapplied to it
    third = await run(decisions.associate_automatically, user, a, c, **AUTO)
    assert third.outcome == "associated" and third.decision.outcome == "automatic_same"
    assert (await root_of(user, a), await root_of(user, b)) == (c, b)
    # joining C's group to B's would join the rejected pair, so that stays blocked
    grouped = await run(decisions.associate_automatically, user, c, b, **AUTO)
    assert grouped.outcome == "blocked"


async def test_user_decisions_outrank_automatic_matching(users):  # noqa: F811
    user, (a, b, _c) = await three(users)
    auto = await run(decisions.associate_automatically, user, a, b, **AUTO)
    assert auto.outcome == "associated"
    assert (await run(decisions.associate_automatically, user, a, b, **AUTO)).outcome == "unchanged"
    await run(decisions.separate_identities, user, a, b)  # user says no: splits and rejects
    assert await active_edges(user) == []
    assert (await run(decisions.associate_automatically, user, a, b, **AUTO)).outcome == "blocked"
    # the user's confirmation also upgrades an automatic association
    other = await run(decisions.associate_automatically, user, a, _c, **AUTO)
    assert other.outcome == "associated"
    upgraded = await run(decisions.merge_identities, user, a, _c)
    assert not upgraded.merged and upgraded.decision.outcome == "confirmed_same"
    async with SessionLocal() as db:
        with pytest.raises(graph.IdentityConflict):
            await decisions.write_decision(
                db,
                user,
                "identity_pair",
                {"first_game_id": min(a, _c), "second_game_id": max(a, _c)},
                "automatic_same",
                decisions.Actor("automatic", "rule", "r"),
                rule_version="1",
                confidence=0.5,
            )


async def test_only_a_user_can_confirm(users):  # noqa: F811
    user, (a, b, _c) = await three(users)
    with pytest.raises(graph.IdentityInvalid):
        await run(decisions.merge_identities, user, a, b, decisions.Actor("automatic", "x"))
    with pytest.raises(graph.IdentityInvalid):
        await run(decisions.merge_identities, user, a, a)


async def test_unresolved_candidates_are_suggested_once_and_never_after_rejection(users):  # noqa: F811
    user, (a, b, c) = await three(users)
    kwargs = {**AUTO}
    first = await run(decisions.associate_automatically, user, a, b, merge=False, **kwargs)
    assert first.outcome == "suggested"
    repeat = await run(decisions.associate_automatically, user, a, b, merge=False, **kwargs)
    assert repeat.outcome == "unchanged"
    assert len(await current(user, "unresolved")) == 1 and await active_edges(user) == []
    await run(decisions.separate_identities, user, a, b)
    assert not await current(user, "unresolved")
    assert (
        await run(decisions.associate_automatically, user, b, a, merge=False, **kwargs)
    ).outcome == "blocked"
    assert (
        await run(decisions.associate_automatically, user, a, c, merge=False, **kwargs)
    ).outcome == ("suggested")


async def test_reversing_a_separate_decision_needs_explicit_confirmation(users):  # noqa: F811
    user, (a, b, c) = await three(users)
    await run(decisions.separate_identities, user, a, b)
    with pytest.raises(graph.IdentityConflict):
        await run(decisions.merge_identities, user, a, b)
    # also through a group: C~A, then merging C into B joins the rejected pair
    await run(decisions.merge_identities, user, c, a)
    with pytest.raises(graph.IdentityConflict):
        await run(decisions.merge_identities, user, c, b)
    result = await run(decisions.merge_identities, user, a, b, replace_separate=True)
    assert result.merged and len(result.superseded_separate) == 1
    assert {await root_of(user, g) for g in (a, b, c)} == {b}


# --- many-to-one, validation, isolation -----------------------------------------------------


async def test_many_to_one_chains_split_at_any_point(users):  # noqa: F811
    user = await users()
    a, b, c, d = [await make_game(user, t) for t in "ABCD"]
    for source in (a, b, c):
        await run(decisions.merge_identities, user, source, d)
    assert {await root_of(user, g) for g in (a, b, c, d)} == {d}
    # a group merged into another keeps its own members under it (a chain)
    e, f = [await make_game(user, t) for t in "EF"]
    await run(decisions.merge_identities, user, e, f)
    await run(decisions.merge_identities, user, d, f)
    assert {await root_of(user, g) for g in (a, b, c, d, e, f)} == {f}
    await run(decisions.unmerge_identity, user, d)  # d takes a, b, c with it
    assert {await root_of(user, g) for g in (a, b, c, d)} == {d}
    assert {await root_of(user, g) for g in (e, f)} == {f}
    await run(decisions.unmerge_identity, user, b, keep_separate=False)
    assert await root_of(user, b) == b and await root_of(user, a) == d
    with pytest.raises(graph.IdentityConflict):
        await run(decisions.unmerge_identity, user, b)  # nothing left to split
    async with SessionLocal() as db:  # decisions spanning the cut were reset, not left stale
        stale = await decisions.list_decisions(db, user, outcome="confirmed_same")
        assert all(
            {x.first_game_id, x.second_game_id} not in ({b, d}, {a, f}, {d, f}) for x in stale
        )


async def test_relationship_validation_blocks_cycles_only_where_meaningless(users):  # noqa: F811
    user, (a, b, c) = await three(users)
    add = graph.add_relationship
    await run(add, user, "dlc_of", a, b)
    await run(add, user, "dlc_of", b, c)
    for bad in ((c, a), (b, a), (c, b)):
        with pytest.raises(graph.IdentityInvalid):
            await run(add, user, "dlc_of", *bad)
    # same edge again is idempotent; self edges, unknown kinds and same_game are refused
    first = await run(add, user, "dlc_of", a, b)
    assert first.ended_at is None
    for args in (("dlc_of", a, a), ("nonsense", a, b), ("same_game", a, b), ("custom", a, b)):
        with pytest.raises(graph.IdentityInvalid):
            await run(add, user, *args)
    with pytest.raises(graph.IdentityInvalid):
        await run(add, user, "related_to", a, b, label="x")
    # intentionally unusual groupings stay possible
    for kind, label in (("related_to", ""), ("custom", "same universe")):
        await run(add, user, kind, a, b, label=label)
        await run(add, user, kind, b, a, label=label)
    # ending an edge allows the opposite direction
    edge = await run(add, user, "port_of", a, b)
    await run(graph.end_relationship, user, edge.id)
    await run(add, user, "port_of", b, a)
    with pytest.raises(graph.IdentityInvalid):
        merge_edge = (await active_edges_for(user))[0]
        await run(graph.end_relationship, user, merge_edge.id)


async def active_edges_for(user):
    await run(decisions.merge_identities, user, *(await two_games(user)))
    return await active_edges(user)


async def two_games(user):
    return await make_game(user, "X"), await make_game(user, "Y")


async def test_users_are_isolated(users):  # noqa: F811
    mine, (a, b, _c) = await three(users)
    theirs = await users()
    other = await make_game(theirs, "Other")
    for fn, args in (
        (decisions.merge_identities, (theirs, a, other)),
        (decisions.unmerge_identity, (theirs, a)),
        (decisions.separate_identities, (theirs, a, other)),
        (graph.add_relationship, (theirs, "related_to", a, other)),
    ):
        with pytest.raises(LookupError):
            await run(fn, *args)
    await run(decisions.merge_identities, mine, a, b)
    assert await current(theirs) == [] and await active_edges(theirs) == []
    await ingest(mine, {"external_id": "9", "title": "A"}, attach={"9": a})
    row = await observation(mine, "9")
    with pytest.raises(LookupError):
        await run(decisions.move_observation, theirs, row.id, other)
    with pytest.raises(LookupError):
        await run(decisions.move_observation, mine, row.id, other)


async def test_concurrent_requests_leave_a_consistent_group(users):  # noqa: F811
    user, (a, b, c) = await three(users)
    results = await asyncio.gather(
        *(run(decisions.merge_identities, user, a, b) for _ in range(4)),
        *(run(decisions.merge_identities, user, b, a) for _ in range(4)),
        return_exceptions=True,
    )
    assert sum(1 for r in results if not isinstance(r, Exception) and r.merged) == 1
    assert not [r for r in results if isinstance(r, Exception)]
    assert len(await active_edges(user)) == 1
    # simultaneous opposing edits and automatic matches never corrupt the graph
    await asyncio.gather(
        run(decisions.merge_identities, user, c, a),
        run(decisions.associate_automatically, user, c, b, **AUTO),
        run(decisions.separate_identities, user, a, b),
        return_exceptions=True,
    )
    edges = await active_edges(user)
    parents = {e.from_game_id: e.to_game_id for e in edges}
    assert len(parents) == len(edges)
    for game in (a, b, c):
        assert await root_of(user, game) not in parents


# --- moving observations --------------------------------------------------------------------


async def test_moving_an_observation_keeps_its_source_id_and_history(users):  # noqa: F811
    user, (a, b, _c) = await three(users)
    await ingest(
        user, {"external_id": "7", "title": "A", "facts": {"playtime_seconds": 5}}, attach={"7": a}
    )
    row = await observation(user, "7")
    moved = await run(decisions.move_observation, user, row.id, b, decisions.Actor(reason="Wrong"))
    assert moved.previous_game_id == a and moved.link.game_id == b
    # repeated syncs of the same record neither duplicate nor move it back
    for stamp in (100, 200):
        await ingest(
            user, {"external_id": "7", "title": "A", "facts": {"playtime_seconds": 6}}, now=stamp
        )
    after = await observation(user, "7")
    assert (after.id, after.external_id) == (row.id, "7") and after.revision == 2
    async with SessionLocal() as db:
        links = list(
            await db.scalars(
                select(GameSourceObservationLink)
                .where(GameSourceObservationLink.observation_id == row.id)
                .order_by(
                    GameSourceObservationLink.linked_at, GameSourceObservationLink.unlinked_at
                )
            )
        )
    assert {link.game_id: link.unlinked_at is None for link in links} == {a: False, b: True}
    assert await run(decisions.observation_match_rejected, user, row.id, a)
    assert not await run(decisions.observation_match_rejected, user, row.id, b)
    again = await run(decisions.move_observation, user, row.id, b)
    assert again.link is None  # idempotent
    back = await run(decisions.move_observation, user, row.id, a)
    assert back.link.game_id == a and not await run(
        decisions.observation_match_rejected, user, row.id, a
    )


# --- the legacy "keep both" review feeds the same decision ----------------------------------


async def test_keep_both_is_a_durable_separate_decision(users):  # noqa: F811
    user, (a, b, _c) = await three(users)
    async with SessionLocal() as db:
        await game_duplicates.keep_both(db, user, a, b)
    assert (await run(decisions.associate_automatically, user, a, b, **AUTO)).outcome == "blocked"
    assert [d.outcome for d in await current(user)] == ["confirmed_separate"]


# --- HTTP -----------------------------------------------------------------------------------


@pytest.fixture
async def api(users):  # noqa: F811
    current_user = {}

    async def make(user_id):
        current_user["id"] = user_id
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=current_user["id"])
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    yield make
    app.dependency_overrides.pop(get_current_user, None)


async def test_http_flow_and_status_codes(users, api):  # noqa: F811
    user, (a, b, c) = await three(users)
    other = await users()
    foreign = await make_game(other, "F")
    async with await api(user) as client:
        merged = await client.post(
            "/api/game-identity/merge", json={"source_id": str(a), "target_id": str(b)}
        )
        assert merged.status_code == 200 and merged.json()["changed"] is True
        repeat = await client.post(
            "/api/game-identity/merge", json={"source_id": str(a), "target_id": str(b)}
        )
        assert repeat.json()["changed"] is False
        effective = await client.get(f"/api/game-identity/games/{a}")
        assert effective.json()["identity_id"] == str(b)
        assert (
            await client.post(
                "/api/game-identity/separate", json={"first_id": str(a), "second_id": str(b)}
            )
        ).status_code == 200
        blocked = await client.post(
            "/api/game-identity/merge", json={"source_id": str(a), "target_id": str(b)}
        )
        assert blocked.status_code == 409
        assert (
            await client.post(
                "/api/game-identity/merge",
                json={"source_id": str(a), "target_id": str(b), "replace_separate": True},
            )
        ).status_code == 200
        unmerged = await client.post("/api/game-identity/unmerge", json={"game_id": str(a)})
        assert (
            unmerged.status_code == 200
            and unmerged.json()["decision"]["outcome"] == "confirmed_separate"
        )
        assert (
            await client.post("/api/game-identity/unmerge", json={"game_id": str(a)})
        ).status_code == 409
        assert (
            await client.post(
                "/api/game-identity/merge", json={"source_id": str(a), "target_id": str(a)}
            )
        ).status_code == 422
        assert (
            await client.post(
                "/api/game-identity/merge", json={"source_id": str(a), "target_id": str(foreign)}
            )
        ).status_code == 404
        assert (
            await client.post(
                "/api/game-identity/merge", json={"source_id": str(a), "target_id": str(c), "x": 1}
            )
        ).status_code == 422
        listed = await client.get("/api/game-identity/decisions?include_superseded=true")
        assert {d["outcome"] for d in listed.json()} >= {"confirmed_same", "confirmed_separate"}
        rel = await client.post(
            "/api/game-identity/relationships",
            json={"kind": "dlc_of", "from_id": str(a), "to_id": str(b)},
        )
        assert rel.status_code == 201
        cycle = await client.post(
            "/api/game-identity/relationships",
            json={"kind": "dlc_of", "from_id": str(b), "to_id": str(a)},
        )
        assert cycle.status_code == 422
        ended = await client.post(f"/api/game-identity/relationships/{rel.json()['id']}/end")
        assert ended.json()["ended_at"] is not None
    async with await api(other) as client:
        assert (await client.get("/api/game-identity/decisions")).json() == []
        assert (await client.get(f"/api/game-identity/games/{a}")).status_code == 404
        assert (
            await client.post(f"/api/game-identity/relationships/{rel.json()['id']}/end")
        ).status_code == 404
