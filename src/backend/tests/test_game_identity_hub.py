"""One game with copies: choosing the main copy and splitting exactly one copy out (#274)."""

from sqlalchemy import func, select, update

from src.database.models.game import Game, GameStatus
from src.database.models.game_archive import GameArchive, GameArchiveVersion
from src.database.models.game_identity import GameRelationship
from src.database.models.media_item import MediaItem
from src.database.session import SessionLocal
from src.features.game_identity import decisions, graph
from tests.test_game_identity_decisions import api, run  # noqa: F401
from tests.test_game_ingestion import make_game, users  # noqa: F401


async def copy(user, title, *, hours=0, status=GameStatus.BACKLOG):
    game_id = await make_game(user, title)
    async with SessionLocal() as db:
        await db.execute(
            update(Game)
            .where(Game.id == game_id)
            .values(playtime_seconds=hours * 3600, status=status)
        )
        await db.commit()
    return game_id


async def layout(user):
    async with SessionLocal() as db:
        merged = await graph.merge_graph(db, user, readonly=True)
    return merged.parent


async def ended_edges(user):
    async with SessionLocal() as db:
        return await db.scalar(
            select(func.count())
            .select_from(GameRelationship)
            .where(GameRelationship.user_id == user, GameRelationship.ended_at.is_not(None))
        )


async def test_make_main_reroots_the_group_as_a_star_and_keeps_history(users):  # noqa: F811
    user = await users()
    steam, epic, switch = [await copy(user, "Hades") for _ in range(3)]
    await run(decisions.merge_identities, user, steam, epic)
    await run(decisions.merge_identities, user, switch, epic)
    assert await layout(user) == {steam: epic, switch: epic}

    result = await run(decisions.make_main, user, steam)

    assert result.changed and result.previous_main_id == epic
    assert await layout(user) == {epic: steam, switch: steam}
    assert await ended_edges(user) == 2  # replaced, not deleted
    again = await run(decisions.make_main, user, steam)
    assert not again.changed and await ended_edges(user) == 2


async def test_make_main_flattens_chains_and_carries_each_copys_display(users):  # noqa: F811
    user = await users()
    a, b, c = [await copy(user, t) for t in ("Java", "Bedrock", "Pocket")]
    await run(decisions.merge_identities, user, a, b)
    await run(decisions.merge_identities, user, b, c)  # chain a -> b -> c
    async with SessionLocal() as db:
        await db.execute(
            update(GameRelationship)
            .where(GameRelationship.from_game_id == a, GameRelationship.ended_at.is_(None))
            .values(display="link")
        )
        await db.commit()

    await run(decisions.make_main, user, b)

    assert await layout(user) == {a: b, c: b}
    async with SessionLocal() as db:
        shown = dict(
            (
                await db.execute(
                    select(GameRelationship.from_game_id, GameRelationship.display).where(
                        GameRelationship.user_id == user, GameRelationship.ended_at.is_(None)
                    )
                )
            ).all()
        )
    assert shown == {a: "link", c: "default"}


async def test_split_copy_removes_only_that_copy(users):  # noqa: F811
    user = await users()
    main, middle, leaf = [await copy(user, "Stardew Valley") for _ in range(3)]
    await run(decisions.merge_identities, user, leaf, middle)
    await run(decisions.merge_identities, user, middle, main)  # leaf -> middle -> main

    result = await run(decisions.split_copy, user, middle)

    assert result.detached == {middle}
    assert await layout(user) == {leaf: main}


async def test_splitting_the_main_copy_promotes_the_most_played_copy(users):  # noqa: F811
    user = await users()
    main = await copy(user, "Hades", hours=1)
    little = await copy(user, "Hades", hours=2)
    most = await copy(user, "Hades", hours=60)
    await run(decisions.merge_identities, user, little, main)
    await run(decisions.merge_identities, user, most, main)

    await run(decisions.split_copy, user, main)

    assert await layout(user) == {little: most}


async def test_merge_can_choose_the_furthest_progressed_copy_as_main(users, api):  # noqa: F811
    user = await users()
    epic = await copy(user, "Hades", hours=4, status=GameStatus.BACKLOG)
    steam = await copy(user, "Hades", hours=61, status=GameStatus.BEATEN)
    async with await api(user) as client:
        merged = await client.post(
            "/api/game-identity/merge",
            json={"source_id": str(steam), "target_id": str(epic), "main": "auto"},
        )
        assert merged.status_code == 200
        assert await layout(user) == {epic: steam}

        moved = await client.post(f"/api/game-identity/games/{epic}/make-main")
        assert moved.status_code == 200
        assert moved.json() == {
            "main_id": str(epic),
            "previous_main_id": str(steam),
            "changed": True,
        }

        split = await client.post(f"/api/game-identity/games/{steam}/split", json={})
        assert split.status_code == 200 and split.json()["detached"] == [str(steam)]
        assert await layout(user) == {}

        lonely = await client.post(f"/api/game-identity/games/{steam}/split", json={})
        assert lonely.status_code == 409


async def test_main_copy_endpoints_are_scoped_to_the_owner(users, api):  # noqa: F811
    owner, intruder = await users(), await users()
    game = await copy(owner, "Celeste")
    async with await api(intruder) as client:
        assert (await client.post(f"/api/game-identity/games/{game}/make-main")).status_code == 404
        assert (
            await client.post(f"/api/game-identity/games/{game}/split", json={})
        ).status_code == 404


# --- the main copy's page shows every copy's content (#274) ----------------------------------


async def add_save(game_id, name):
    async with SessionLocal() as db:
        archive = GameArchive(game_id=game_id, kind="save", name=name, tags=[])
        db.add(archive)
        await db.flush()
        db.add(GameArchiveVersion(archive_id=archive.id, filename="save.zip", size=10))
        await db.commit()
        return archive.id


async def add_screenshot(game_id, filename):
    async with SessionLocal() as db:
        db.add(MediaItem(game_id=game_id, kind="screenshot", filename=filename, tags=[]))
        await db.commit()


async def test_main_page_lists_saves_and_media_of_every_copy(users, api):  # noqa: F811
    user = await users()
    steam = await copy(user, "Hades", hours=61)
    epic = await copy(user, "Hades", hours=4)
    switch = await copy(user, "Hades")
    await run(decisions.merge_identities, user, steam, epic)  # Epic is the main copy
    await run(decisions.merge_identities, user, switch, epic)
    async with SessionLocal() as db:  # the Switch copy is listed as its own library entry
        await db.execute(
            update(GameRelationship)
            .where(GameRelationship.from_game_id == switch, GameRelationship.ended_at.is_(None))
            .values(display="link")
        )
        await db.commit()
    steam_save = await add_save(steam, "Playnite save")
    await add_save(switch, "Switch save")
    await add_screenshot(steam, "steam.png")

    async with await api(user) as client:
        own = await client.get(f"/api/game/{epic}/archives/save")
        assert own.status_code == 200 and own.json() == []  # unchanged default contract

        grouped = (await client.get(f"/api/game/{epic}/archives/save?group=true")).json()
        assert [a["id"] for a in grouped] == [str(steam_save)]
        assert grouped[0]["game_id"] == str(steam)
        assert grouped[0]["copy"]["is_main"] is False
        assert grouped[0]["versions"][0]["url"].startswith(f"/api/game/{steam}/archives/")

        media = (await client.get(f"/api/game/{epic}/screenshots?group=true")).json()["media"]
        assert [m["copy"]["game_id"] for m in media] == [str(steam)]

        counts = (await client.get(f"/api/game/{epic}/content-counts?group=true")).json()
        assert (counts["saves"], counts["screenshots"]) == (1, 1)

        # A copy's own page lists only its own content, even with group=true.
        copy_page = (await client.get(f"/api/game/{steam}/archives/save?group=true")).json()
        assert [a["copy"]["is_main"] for a in copy_page] == [True]
        separate = (await client.get(f"/api/game/{switch}/archives/save?group=true")).json()
        assert [a["name"] for a in separate] == ["Switch save"]


async def test_grouped_listing_never_crosses_users(users, api):  # noqa: F811
    owner, intruder = await users(), await users()
    game = await copy(owner, "Celeste")
    await add_save(game, "Mine")
    async with await api(intruder) as client:
        response = await client.get(f"/api/game/{game}/archives/save?group=true")
        assert response.status_code == 404


async def test_main_page_trash_lists_every_copys_trashed_items(users, api):  # noqa: F811
    user = await users()
    main = await copy(user, "Hades", hours=9)
    steam = await copy(user, "Hades")
    await run(decisions.merge_identities, user, steam, main)
    archive = await add_save(steam, "Old Steam save")
    async with SessionLocal() as db:
        db.add(
            MediaItem(game_id=steam, kind="screenshot", filename="gone.png", tags=[], deleted_at=1)
        )
        row = await db.get(GameArchive, archive)
        row.deleted_at = 1
        await db.commit()
    async with await api(user) as client:
        assert (await client.get(f"/api/game/{main}/archives/save/trash")).json() == []
        saves = (await client.get(f"/api/game/{main}/archives/save/trash?group=true")).json()
        assert [(s["game_id"], s["copy"]["is_main"]) for s in saves] == [(str(steam), False)]
        media = (await client.get(f"/api/game/{main}/screenshots/trash?group=true")).json()
        assert [m["copy"]["game_id"] for m in media["media"]] == [str(steam)]
        files = (await client.get(f"/api/game/{main}/files/doc/trash?group=true")).json()
        assert files == {"files": []}
