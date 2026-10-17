"""Safe migration of a populated library into the identity model (#102)."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, insert, select

from src.database.models.achievement import Achievement
from src.database.models.achievement_override import AchievementOverride
from src.database.models.game import Game, GameLink, GameStatus
from src.database.models.game_duplicate_dismissal import GameDuplicateDismissal
from src.database.models.game_identity import (
    GameIdentity,
    GameIdentityDecision,
    GameRelationship,
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationLink,
    GameSourceObservationRevision,
    GameUmbrella,
)
from src.database.models.game_identity_backfill import GameIdentityBackfill
from src.database.models.game_identity_tracking import (
    GameFieldValue,
    GameMetricCounter,
    GameMetricCounterMember,
)
from src.database.models.game_note_detail import GameNoteDetail
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.game_identity import decisions, graph, overview
from src.features.game_identity_backfill import runner, stages


@pytest.fixture
async def users():
    """Users whose library is removed with them (`game_links` has no delete rule, audit H3)."""
    created: list[UUID] = []

    async def make() -> UUID:
        user_id = uuid4()
        async with SessionLocal() as db:
            await db.execute(
                insert(User).values(
                    id=user_id, username=user_id.hex, email=f"{user_id}@x.test", password_hash="x"
                )
            )
            await db.commit()
        created.append(user_id)
        return user_id

    yield make
    async with SessionLocal() as db:
        owned = select(Game.id).where(Game.user_id.in_(created))
        await db.execute(delete(GameLink).where(GameLink.game_id.in_(owned)))
        await db.execute(delete(User).where(User.id.in_(created)))
        await db.commit()


TABLES = (
    GameUmbrella,
    GameIdentity,
    GameSourceConnection,
    GameSourceObservation,
    GameSourceObservationRevision,
    GameSourceObservationLink,
    GameIdentityDecision,
    GameRelationship,
    GameMetricCounter,
    GameMetricCounterMember,
    GameFieldValue,
)
NOW = 1_700_000_000


async def add_game(user, title, **values) -> UUID:
    game_id = values.pop("id", uuid4())
    async with SessionLocal() as db:
        await db.execute(
            insert(Game).values(
                id=game_id,
                user_id=user,
                title=title,
                sort_title=title.lower(),
                folder_location=game_id.hex,
                created_at=values.pop("created_at", NOW),
                **values,
            )
        )
        await db.commit()
    return game_id


async def populate(user) -> dict[str, UUID]:
    """A representative populated library, as an installation before the identity model."""
    g: dict[str, UUID] = {}
    g["steam"] = await add_game(
        user,
        "Portal 2",
        source="Steam",
        external_id="620",
        platform="PC",
        status=GameStatus.PLAYED,
        playtime_seconds=7200,
        last_played_at=NOW - 500,
        notes="remember the cake",
        collections=["Puzzle"],
        created_at=NOW + 1,
    )
    g["wishlist"] = await add_game(
        user,
        "Steam app 999",
        source="Steam",
        external_id="999",
        status=GameStatus.WISHLIST,
        created_at=NOW + 2,
    )
    g["playnite"] = await add_game(
        user,
        "Hades",
        source="Steam",
        platform="PC",
        playnite_guid=uuid4(),
        favorite=True,
        status=GameStatus.PLAYING,
        playtime_seconds=36000,
        last_played_at=NOW - 10,
        created_at=NOW + 3,
    )
    g["manual"] = await add_game(
        user,
        "Pen and Paper Night",
        source="physical",
        status=GameStatus.BEATEN,
        playtime_seconds=600,
        locked_fields=["title"],
        created_at=NOW + 4,
    )
    g["main"] = await add_game(
        user,
        "Elden Ring",
        source="Steam",
        external_id="1245620",
        platform="PC",
        status=GameStatus.BACKLOG,
        playtime_seconds=100,
        created_at=NOW + 5,
    )
    g["copy"] = await add_game(
        user,
        "Elden Ring",
        source="physical",
        platform="PlayStation 5",
        parent_game_id=g["main"],
        relationship_type="owned_copy",
        status=GameStatus.PLAYING,
        playtime_seconds=50,
        created_at=NOW + 6,
    )
    g["dlc"] = await add_game(
        user,
        "Shadow of the Erdtree",
        source="Steam",
        external_id="2778580",
        parent_game_id=g["main"],
        relationship_type="dlc",
        status=GameStatus.BACKLOG,
        created_at=NOW + 7,
    )
    g["doom_a"] = await add_game(user, "Doom", source="physical", created_at=NOW + 8)
    g["doom_b"] = await add_game(user, "Doom", source="GOG", created_at=NOW + 9)
    g["twin_a"] = await add_game(user, "Hollow Knight", source="physical", created_at=NOW + 10)
    g["twin_b"] = await add_game(user, "Hollow Knight", source="physical", created_at=NOW + 11)
    g["dup_a"] = await add_game(
        user,
        "Dota 2",
        source="Steam",
        external_id="570",
        status=GameStatus.PLAYED,
        playtime_seconds=900,
        created_at=NOW + 12,
    )
    g["dup_b"] = await add_game(
        user,
        "Dota 2",
        source="Steam",
        external_id="570",
        status=GameStatus.PLAYED,
        playtime_seconds=300,
        created_at=NOW + 13,
    )
    g["plugin"] = await add_game(
        user, "Fortnite", source="Epic Games", external_id="fn-1", created_at=NOW + 14
    )
    g["orphan"] = await add_game(
        user, "Old Playnite Game", playnite_guid=uuid4(), deleted_at=NOW + 99, created_at=NOW + 15
    )
    async with SessionLocal() as db:
        db.add_all(
            [
                Achievement(
                    game_id=g["steam"],
                    provider="Steam",
                    external_id=f"a{n}",
                    name=f"A{n}",
                    unlocked=n < 4,
                    created_at=NOW,
                )
                for n in range(10)
            ]
            + [
                AchievementOverride(
                    game_id=g["manual"],
                    provider="manual",
                    external_id="m1",
                    name="Finished the campaign",
                    unlocked=True,
                ),
                GameLink(game_id=g["steam"], label="Wiki", url="https://wiki.example/portal"),
                GameNoteDetail(game_id=g["steam"], name="walkthrough"),
                GameDuplicateDismissal(
                    user_id=user,
                    first_game_id=min(g["doom_a"], g["doom_b"]),
                    second_game_id=max(g["doom_a"], g["doom_b"]),
                    created_at=NOW + 50,
                ),
            ]
        )
        await db.commit()
    return g


async def counts(user) -> dict[str, int]:
    async with SessionLocal() as db:
        return {
            model.__tablename__: await db.scalar(
                select(func.count()).select_from(model).where(model.user_id == user)
            )
            for model in TABLES
        }


async def fresh(user_id) -> dict:
    return await runner.run_user(user_id, batch_size=4)


async def rows(model, user, *where):
    async with SessionLocal() as db:
        return list(await db.scalars(select(model).where(model.user_id == user, *where)))


async def test_backfill_maps_every_game_and_a_second_run_changes_nothing(users):  # noqa: F811
    user = await users()
    g = await populate(user)
    first = await fresh(user)
    assert first["status"] == "completed", first
    assert first["errors"] == [] and first["before"]["games"] == 15
    after_first = await counts(user)
    second = await fresh(user)
    assert second["status"] == "completed"
    assert await counts(user) == after_first  # nothing duplicated
    assert not any(
        second["counts"].get(name)
        for name in (
            "observations_created",
            "links_created",
            "umbrellas_created",
            "identities_created",
            "relationships_created",
            "dismissals_migrated",
            "titles_tracked",
            "completion_tracked",
        )
    )
    async with SessionLocal() as db:
        ids = set(await db.scalars(select(Game.id).where(Game.user_id == user)))
        placed = set(
            await db.scalars(select(GameIdentity.game_id).where(GameIdentity.user_id == user))
        )
        linked = set(
            await db.scalars(
                select(GameSourceObservationLink.game_id).where(
                    GameSourceObservationLink.user_id == user,
                    GameSourceObservationLink.unlinked_at.is_(None),
                )
            )
        )
    assert ids == set(g.values()) == placed == linked  # IDs preserved, all placed and sourced


async def test_references_and_user_data_survive(users):  # noqa: F811
    user = await users()
    g = await populate(user)
    report = await fresh(user)
    for name in (
        "achievements",
        "achievement_overrides",
        "game_links",
        "game_notes",
        "duplicate_dismissals",
        "games",
    ):
        assert report["after"][name] == report["before"][name]
    assert report["after"]["playtime_seconds"] == report["before"]["playtime_seconds"]
    assert (
        report["after"]["games_with_collections"] == 1 and report["after"]["games_with_notes"] == 1
    )
    async with SessionLocal() as db:
        steam = await db.get(Game, g["steam"])
        assert (steam.notes, steam.collections, steam.playtime_seconds) == (
            "remember the cake",
            ["Puzzle"],
            7200,
        )
        copy = await db.get(Game, g["copy"])  # the old columns are untouched
        assert (copy.parent_game_id, copy.relationship_type) == (g["main"], "owned_copy")


async def test_identity_structure_matches_the_mapping(users):  # noqa: F811
    user = await users()
    g = await populate(user)
    await fresh(user)
    async with SessionLocal() as db:
        umbrella = {
            row.game_id: row.umbrella_id
            for row in await db.scalars(select(GameIdentity).where(GameIdentity.user_id == user))
        }
        merge = await graph.merge_graph(db, user)
    assert umbrella[g["main"]] == umbrella[g["copy"]]  # owned copy group: one umbrella
    assert merge.root(g["copy"]) == g["main"]
    assert umbrella[g["dlc"]] != umbrella[g["main"]]  # a DLC is its own game
    edges = await rows(GameRelationship, user, GameRelationship.kind == "dlc_of")
    assert [(e.from_game_id, e.to_game_id, e.origin) for e in edges] == [
        (g["dlc"], g["main"], "migration")
    ]
    # titles alone never merge: equal titles stay apart
    for a, b in (("twin_a", "twin_b"), ("doom_a", "doom_b"), ("dup_a", "dup_b")):
        assert umbrella[g[a]] != umbrella[g[b]] and merge.root(g[a]) != merge.root(g[b])
    # the earlier "keep both" is a durable separate decision
    separate = await rows(
        GameIdentityDecision, user, GameIdentityDecision.outcome == "confirmed_separate"
    )
    assert {(d.first_game_id, d.second_game_id, d.actor_kind) for d in separate} == {
        (min(g["doom_a"], g["doom_b"]), max(g["doom_a"], g["doom_b"]), "migration")
    }
    # the shared Steam record is queued for review, never merged
    queued = await rows(GameIdentityDecision, user, GameIdentityDecision.outcome == "unresolved")
    assert any({d.first_game_id, d.second_game_id} == {g["dup_a"], g["dup_b"]} for d in queued)
    async with SessionLocal() as db:
        primaries = await db.scalar(
            select(func.count())
            .select_from(GameIdentity)
            .where(GameIdentity.user_id == user, GameIdentity.is_primary.is_(True))
        )
        umbrellas = await db.scalar(
            select(func.count()).select_from(GameUmbrella).where(GameUmbrella.user_id == user)
        )
    assert primaries == umbrellas == 14  # 15 games, one group of two


async def test_sources_are_attributed_without_guessing(users):  # noqa: F811
    user = await users()
    g = await populate(user)
    await fresh(user)
    obs = {
        (c.source_kind, c.source, c.source_scope, o.external_id): o for c, o in (await _pairs(user))
    }
    steam = obs[("host", "steam", "legacy", "620")]
    assert steam.observed_facts["playtime_seconds"] == 7200
    assert steam.observed_facts["achievements_total"] == 10
    assert steam.observed_facts["achievements_unlocked"] == 4
    assert steam.observed_facts["owned"] is True
    wish = obs[("host", "steam", "legacy", "999")]
    assert wish.observed_title is None  # the placeholder is not a source name
    assert wish.observed_facts == {"wishlisted": True}
    assert ("plugin", "legacy-unattributed", "legacy", "fn-1") in obs
    assert ("manual", "manual", "local", str(g["manual"])) in obs
    guid = (await _game(g["playnite"])).playnite_guid
    assert obs[("host", "playnite", "legacy", str(guid))].observed_facts["status"] == "PLAYING"
    # a second row for the same Steam record keeps its own observation in a scoped connection
    dup_scopes = [k for k in obs if k[3] == "570"]
    assert len(dup_scopes) == 2 and any(k[2].startswith("legacy-duplicate:") for k in dup_scopes)
    # a trashed Playnite row still has its record, and nothing was marked unavailable
    assert all(o.availability == "available" for o in obs.values())


async def _pairs(user):
    async with SessionLocal() as db:
        found = await db.execute(
            select(GameSourceConnection, GameSourceObservation)
            .join(
                GameSourceObservation,
                GameSourceObservation.connection_id == GameSourceConnection.id,
            )
            .where(GameSourceConnection.user_id == user)
        )
        return [(c, o) for c, o in found]


async def _game(game_id) -> Game:
    async with SessionLocal() as db:
        return await db.get(Game, game_id)


async def test_provenance_and_manual_edits_are_protected(users):  # noqa: F811
    user = await users()
    g = await populate(user)
    report = await fresh(user)
    titles = {
        v.game_id: v for v in await rows(GameFieldValue, user, GameFieldValue.field == "title")
    }
    assert titles[g["manual"]].locked and titles[g["manual"]].lock_origin == "legacy"
    assert titles[g["wishlist"]].quality == "placeholder"
    statuses = {
        v.game_id: v
        for v in await rows(GameFieldValue, user, GameFieldValue.field == "completion_status")
    }
    assert statuses[g["manual"]].locked and statuses[g["manual"]].origin == "legacy"
    assert "status" in (await _game(g["manual"])).locked_fields
    assert not statuses[g["playnite"]].locked  # Playnite owns its games' status
    assert "status" not in (await _game(g["playnite"])).locked_fields
    assert not statuses[g["main"]].locked  # a backlog status is not a protected choice
    assert report["counts"]["statuses_protected"] >= 1
    counters = await rows(GameMetricCounter, user, GameMetricCounter.metric == "playtime")
    assert {c.game_id for c in counters} >= {g["steam"], g["playnite"], g["manual"]}


async def test_ambiguous_records_are_left_for_review(users):  # noqa: F811
    user = await users()
    g = await populate(user)
    await fresh(user)
    merged = await rows(GameRelationship, user, GameRelationship.kind == "same_game")
    assert {(e.from_game_id, e.to_game_id) for e in merged} == {(g["copy"], g["main"])}
    async with SessionLocal() as db:
        assert (await overview.library_layout(db, user))["groups"][0]["umbrella_id"] == g["main"]
        again = await decisions.list_decisions(db, user)
    assert again  # decisions are readable through the existing API


async def test_resume_after_a_failed_stage_and_a_failed_chunk(users, monkeypatch):  # noqa: F811
    user, twin = await users(), await users()
    await populate(user)
    await populate(twin)
    reference = await fresh(twin)
    calls = {"n": 0}
    original = stages._observe_chunk

    async def flaky(db, p, batch, winners):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated crash")
        await original(db, p, batch, winners)

    monkeypatch.setattr(stages, "_observe_chunk", flaky)
    failed = await fresh(user)
    assert failed["status"] == "failed" and failed["completed_stages"] == []
    assert failed["errors"][0]["stage"] == "observations"
    assert "RuntimeError" in failed["errors"][0]["code"]
    partial = (await counts(user))["game_source_observations"]
    assert 0 < partial < reference["after"]["observations"]  # two chunks committed, one rolled back
    monkeypatch.setattr(stages, "_observe_chunk", original)
    resumed = await fresh(user)
    assert resumed["status"] == "completed" and resumed["attempts"] == 2
    assert await counts(user) == await counts(twin)  # same end state as an uninterrupted run


async def test_a_failed_later_stage_resumes_where_it_stopped(users, monkeypatch):  # noqa: F811
    user = await users()
    await populate(user)

    async def broken(p, size):
        raise ConnectionError("database restarted")

    monkeypatch.setattr(
        runner,
        "STAGES",
        tuple((name, broken if name == "relationships" else fn) for name, fn in runner.STAGES),
    )
    failed = await fresh(user)
    assert failed["status"] == "failed"
    assert failed["completed_stages"] == ["observations", "merges", "placement"]
    monkeypatch.undo()
    resumed = await fresh(user)
    assert resumed["status"] == "completed"
    assert resumed["completed_stages"][-1] == "matching"
    assert len(await rows(GameRelationship, user, GameRelationship.kind == "dlc_of")) == 1


async def test_a_second_runner_is_refused_while_one_holds_the_user(users):  # noqa: F811
    user = await users()
    await populate(user)
    async with SessionLocal() as holder:
        key = await holder.scalar(select(func.hashtextextended(runner._lock_key(user), 102)))
        assert await holder.scalar(select(func.pg_try_advisory_lock(key)))
        with pytest.raises(runner.BackfillBusy):
            await runner.run_user(user)
        await holder.execute(select(func.pg_advisory_unlock(key)))
    assert (await runner.run_user(user))["status"] == "completed"


async def test_users_are_isolated(users):  # noqa: F811
    user, other = await users(), await users()
    await populate(user)
    mine = await add_game(other, "Portal 2", source="Steam", external_id="620")
    await fresh(user)
    assert (await counts(other))["game_source_observations"] == 0  # not touched by user's run
    assert await rows(GameIdentityBackfill, other) == []
    await fresh(other)
    # the same Steam appid for two users are two records
    steam = [o for c, o in await _pairs(user) + await _pairs(other) if o.external_id == "620"]
    assert len(steam) == 2 and len({o.user_id for o in steam}) == 2
    assert (await counts(other))["game_identities"] == 1
    assert (await _game(mine)).user_id == other


async def test_trashed_and_new_games_are_picked_up_on_the_next_run(users):  # noqa: F811
    user = await users()
    g = await populate(user)
    await fresh(user)
    late = await add_game(user, "Late Arrival", source="Steam", external_id="1")
    report = await fresh(user)
    assert report["status"] == "completed" and report["counts"]["identities_created"] == 1
    async with SessionLocal() as db:
        assert await db.scalar(select(GameIdentity.game_id).where(GameIdentity.game_id == late))
        trashed = await db.scalar(
            select(GameIdentity.game_id).where(GameIdentity.game_id == g["orphan"])
        )
    assert trashed == g["orphan"]


async def test_a_user_decision_made_before_the_backfill_is_kept(users):  # noqa: F811
    user = await users()
    g = await populate(user)
    # the person already split the owned copy group with the new tools
    async with SessionLocal() as db:
        await decisions.separate_identities(db, user, g["copy"], g["main"])
        await db.commit()
    report = await fresh(user)
    assert report["status"] == "completed"
    assert {e["code"] for e in report["errors"]} == {"owned_copy_kept_separate"}
    assert await rows(GameRelationship, user, GameRelationship.kind == "same_game") == []
    async with SessionLocal() as db:
        ids = {
            row.game_id: row.umbrella_id
            for row in await db.scalars(select(GameIdentity).where(GameIdentity.user_id == user))
        }
    assert ids[g["copy"]] != ids[g["main"]]


async def test_sync_after_a_user_marks_a_source_unlinked_keeps_that_choice(users):  # noqa: F811
    user = await users()
    g = await populate(user)
    await fresh(user)
    async with SessionLocal() as db:
        link = await db.scalar(
            select(GameSourceObservationLink).where(
                GameSourceObservationLink.user_id == user,
                GameSourceObservationLink.game_id == g["steam"],
            )
        )
        link.unlinked_at = link.linked_at + 1
        await db.commit()
    again = await fresh(user)
    assert again["counts"].get("links_created", 0) == 0  # a person's unlink is not undone
    assert again["counts"]["links_elsewhere"] >= 1


async def test_dry_run_plan_writes_nothing(users):  # noqa: F811
    user = await users()
    await populate(user)
    async with SessionLocal() as db:
        plan = await runner.plan(db, user)
    assert plan["games"] == 15 and plan["games_without_identity"] == 15
    assert plan["owned_copy_links"] == 1 and plan["other_relationships"] == 1
    assert plan["shared_source_record_groups"] == 1
    assert (await counts(user))["game_identities"] == 0
    assert await rows(GameIdentityBackfill, user) == []


async def test_failed_invariants_are_reported_not_hidden():
    status, findings = runner.judge(
        {"games": 5, "achievements": 3}, {"games": 4, "achievements": 3}
    )
    assert status == "failed" and findings[0]["code"] == "decreased:games"
    status, findings = runner.judge({"games": 5}, {"games": 5, "games_without_identity": 2})
    assert status == "incomplete" and findings[0]["count"] == 2
    assert runner.judge({"games": 5}, {"games": 6})[0] == "completed"


async def test_one_batch_may_mix_records_that_report_different_fields(users):  # noqa: F811
    """Regression: a multi-row insert needs the same columns for every record."""
    from src.features.game_ingestion.contract import (
        ObservationBatchV1,
        SourceConnectionRef,
        SourceObservationV1,
    )
    from src.features.game_ingestion.service import ingest_batch

    user = await users()
    connection = SourceConnectionRef(source_kind="host", source="steam", source_scope="legacy")
    batch = ObservationBatchV1(
        connection=connection,
        observations=[
            SourceObservationV1(external_id="1", title="A", platform="PC"),
            SourceObservationV1(external_id="2"),
            SourceObservationV1(external_id="3", edition="Deluxe"),
        ],
    )
    async with SessionLocal() as db:
        result = await ingest_batch(db, user, batch)
        await db.commit()
    assert result.count("created") == 3
    stored = {o.external_id: o for o in await rows(GameSourceObservation, user)}
    assert stored["1"].observed_platform == "PC" and stored["2"].observed_title is None
    assert stored["3"].observed_edition == "Deluxe"
