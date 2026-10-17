"""Manual achievements and completion alongside imported data (#97)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from src.api.routes.library_sync import _replace_achievements
from src.core.auth import AuthenticatedActor, get_current_actor, get_current_user
from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.game_identity_tracking import GameManualAchievement
from src.database.session import SessionLocal
from src.features.game_identity import counters, decisions, manual
from src.features.game_identity.graph import IdentityConflict, IdentityInvalid
from src.features.imports.game_progression import apply_provider_progress
from src.features.imports.library_export import build_library_export
from src.main import app
from tests.test_game_counters import run
from tests.test_game_ingestion import make_game, users  # noqa: F401


def row(ext, name, unlocked=False):
    return {"external_id": ext, "name": name, "unlocked": unlocked}


async def seed(game, provider, *rows):
    async with SessionLocal() as db:
        await _replace_achievements(db, game, provider, list(rows))
        await db.commit()


async def add(user, game, title="Find the key", **values):
    return await run(manual.create_achievement, user, game, {"title": title, **values})


async def tracked(user, game):
    return await run(manual.achievements_view, user, game)


async def merge(user, first, second):
    await run(decisions.merge_identities, user, first, second, decisions.Actor(reason="t"))


async def test_create_edit_earn_unearn_and_remove(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Obscure Game")
    made = await add(user, game, "  Find the key ", description="Behind the door", category="Story")
    assert made.title == "Find the key" and not made.achieved and made.achieved_at is None

    earned = await run(manual.set_earned, user, made.id, True)
    assert earned.achieved and earned.achieved_at is not None
    edited = await run(manual.update_achievement, user, made.id, {"title": "Find both keys"})
    assert edited.title == "Find both keys" and edited.achieved_at == earned.achieved_at

    backdated = await run(manual.set_earned, user, made.id, True, 1_700_000_000)
    assert backdated.achieved_at == 1_700_000_000
    undone = await run(manual.set_earned, user, made.id, False)
    assert not undone.achieved and undone.achieved_at is None

    view = await tracked(user, game)
    assert view["umbrella"]["total"] == 1 and view["umbrella"]["unlocked"] == 0
    await run(manual.remove_achievement, user, made.id)
    assert (await tracked(user, game))["umbrella"]["total"] == 0
    async with SessionLocal() as db:  # removal archives; the row is kept
        kept = await db.get(GameManualAchievement, made.id)
        assert kept is not None and kept.archived_at is not None
    with pytest.raises(LookupError):
        await run(manual.set_earned, user, made.id, True)


async def test_validation(users):  # noqa: F811
    user = await users()
    game = await make_game(user)
    for bad in ({"title": "   "}, {"title": "x", "icon_url": "file:///etc/passwd"}):
        with pytest.raises(IdentityInvalid):
            await run(manual.create_achievement, user, game, bad)
    with pytest.raises(IdentityInvalid):
        await run(
            manual.create_achievement,
            user,
            game,
            {"title": "x", "icon_url": "http://u:p@a.test/i.png"},
        )
    made = await add(user, game, icon_url="https://example.test/i.png")
    with pytest.raises(IdentityInvalid):
        await run(manual.update_achievement, user, made.id, {"title": ""})
    with pytest.raises(IdentityInvalid):
        await run(manual.update_achievement, user, made.id, {"achieved_at": -5})
    with pytest.raises(IdentityConflict):  # no umbrella has been assigned yet
        await run(manual.create_achievement, user, game, {"title": "u"}, umbrella=True)


async def test_users_are_isolated(users):  # noqa: F811
    one, two = await users(), await users()
    game = await make_game(one)
    made = await add(one, game)
    with pytest.raises(LookupError):
        await run(manual.create_achievement, two, game, {"title": "x"})
    for action in (
        lambda db: manual.set_earned(db, two, made.id, True),
        lambda db: manual.update_achievement(db, two, made.id, {"title": "x"}),
        lambda db: manual.remove_achievement(db, two, made.id),
        lambda db: manual.achievements_view(db, two, game),
        lambda db: manual.set_completion(db, two, game, GameStatus.BEATEN),
        lambda db: manual.completion_view(db, two, game),
    ):
        async with SessionLocal() as db:
            with pytest.raises(LookupError):
                await action(db)
    assert (await tracked(one, game))["umbrella"]["total"] == 1


async def test_manual_and_provider_overlap_and_duplicate_provider_ids(users):  # noqa: F811
    user = await users()
    a, b = await make_game(user, "Game PC"), await make_game(user, "Game Deck")
    await seed(a, "Steam", row("1", "Win", True), row("2", "Win"), row("3", "Explore"))
    await seed(b, "Steam", row("1", "Win"), row("3", "Explore", True))
    solo = await tracked(user, a)
    assert solo["umbrella"]["total"] == 3  # equal titles, different IDs stay two
    assert [v["game_id"] for v in solo["versions"]] == [a]

    await add(user, a, "Win", achieved=True)  # same title as a provider one: still separate
    assert (await tracked(user, a))["umbrella"] == {
        **(await tracked(user, a))["umbrella"],
        "total": 4,
        "unlocked": 2,
        "manual": 1,
        "provider": 3,
    }

    await merge(user, b, a)
    both = await tracked(user, b)
    # the same Steam IDs on both rows are one achievement each: 3 provider + 1 manual
    assert both["umbrella"]["total"] == 4 and both["umbrella"]["unlocked"] == 3
    by_game = {v["game_id"]: v for v in both["versions"]}
    assert by_game[a]["provider"] == {"total": 3, "unlocked": 1}
    assert by_game[b]["provider"] == {"total": 2, "unlocked": 1}
    assert (
        by_game[a]["manual"] == {"total": 1, "unlocked": 1} and by_game[b]["manual"]["total"] == 0
    )

    metrics = (await run(counters.reconcile, user, b))["metrics"]["achievements"]
    assert (metrics["unlocked"], metrics["total"], metrics["exact"]) == (3, 4, True)

    await run(decisions.unmerge_identity, user, b, decisions.Actor(reason="t"))  # split
    assert (await tracked(user, b))["umbrella"]["total"] == 2
    assert (await tracked(user, a))["umbrella"]["total"] == 4


async def test_match_key_is_the_users_explicit_link_across_versions(users):  # noqa: F811
    user = await users()
    a, b = await make_game(user, "A"), await make_game(user, "B")
    first = await add(user, a, "Platinum", match_key="plat", achieved=True)
    await add(user, b, "Platinum trophy", match_key=" PLAT ")
    await add(user, b, "Platinum", achieved=False)  # same title, no key: not linked
    await merge(user, b, a)
    view = await tracked(user, a)
    assert view["umbrella"]["total"] == 2 and view["umbrella"]["unlocked"] == 1
    assert first.id in {m["id"] for m in view["manual"]}


async def test_repeated_sync_never_touches_manual_state(users):  # noqa: F811
    user = await users()
    game = await make_game(user)
    made = await add(user, game, "Mine")
    await run(manual.set_earned, user, made.id, True, 1_700_000_000)
    for unlocked in (False, True, False):
        await seed(game, "Steam", row("9", "Synced", unlocked))
        await seed(game, "Steam", row("9", "Synced", unlocked))
    async with SessionLocal() as db:
        kept = await db.get(GameManualAchievement, made.id)
        assert kept.achieved and kept.achieved_at == 1_700_000_000 and kept.title == "Mine"
        assert (
            len((await db.scalars(select(Achievement).where(Achievement.game_id == game))).all())
            == 1
        )
    assert (await tracked(user, game))["umbrella"]["total"] == 2


async def test_completion_is_partial_per_version_and_survives_sync(users):  # noqa: F811
    user = await users()
    a, b = await make_game(user, "Game PC"), await make_game(user, "Game Deck")
    await merge(user, b, a)
    assert (await run(manual.completion_view, user, a))[
        "summary"
    ] == "Not completed on any of 2 tracked versions"

    await run(manual.set_completion, user, a, GameStatus.BEATEN)
    view = await run(manual.completion_view, user, b)
    assert view["summary"] == "Completed on 1 of 2 tracked versions" and view["partial"]
    flags = {v["game_id"]: (v["completed"], v["user_set"]) for v in view["versions"]}
    assert flags == {a: (True, True), b: (False, False)}

    await run(manual.set_completion, user, a, GameStatus.BACKLOG)  # chosen by hand, then synced
    async with SessionLocal() as db:  # provider progress cannot move a hand-set status
        game = await db.get(Game, a)
        apply_provider_progress(game, owned=True, total_achievements=2, unlocked_achievements=2)
        assert game.status == GameStatus.BACKLOG
        other = await db.get(Game, b)
        apply_provider_progress(other, owned=True, total_achievements=2, unlocked_achievements=2)
        assert other.status == GameStatus.MASTERED  # unlocked rows keep their old behaviour
        await db.commit()
    now = await run(manual.completion_view, user, a)
    assert (now["completed"], now["of"]) == (1, 2)

    await run(manual.set_completion, user, a, GameStatus.PLAYING)  # uncomplete
    again = await run(manual.completion_view, user, a)
    assert again["completed"] == 1 and again["of"] == 2
    await run(manual.set_completion, user, b, GameStatus.PLAYING)
    assert (await run(manual.completion_view, user, a))["all_completed"] is False

    history = await run(manual.completion_history, user, a)
    assert history[0]["status"] == "PLAYING" and history[0]["state"] == "current"
    assert {h["status"] for h in history[1:]} == {"BACKLOG", "BEATEN"}
    assert {h["state"] for h in history[1:]} == {"superseded"}

    await run(manual.release_completion, user, a)
    released = next(
        v for v in (await run(manual.completion_view, user, a))["versions"] if v["game_id"] == a
    )
    assert not released["user_set"]


async def test_all_complete_summary(users):  # noqa: F811
    user = await users()
    a, b = await make_game(user), await make_game(user)
    await run(manual.set_completion, user, a, GameStatus.MASTERED)
    assert (await run(manual.completion_view, user, a))["summary"] == "Completed"
    await merge(user, b, a)
    await run(manual.set_completion, user, b, GameStatus.BEATEN)
    done = await run(manual.completion_view, user, a)
    assert done["summary"] == "Completed on all 2 tracked versions" and done["all_completed"]


@asynccontextmanager
async def client_for(user, kind="session"):
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user)
    app.dependency_overrides[get_current_actor] = lambda: AuthenticatedActor(user, kind)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_actor, None)


async def test_http_flow_and_isolation(users):  # noqa: F811
    user, other = await users(), await users()
    game = await make_game(user, "Obscure")
    base = "/api/game-identity"
    async with client_for(user) as client:
        created = await client.post(
            f"{base}/games/{game}/manual-achievements",
            json={"title": "First", "description": "d", "icon_url": "https://example.test/a.png"},
        )
        assert created.status_code == 201
        item = created.json()
        assert item["icon_url"].endswith(f"/{item['id']}/icon") and not item["achieved"]
        earned = await client.put(
            f"{base}/manual-achievements/{item['id']}/earned", json={"achieved": True}
        )
        assert earned.json()["achieved"] and earned.json()["achieved_at"]
        patched = await client.patch(
            f"{base}/manual-achievements/{item['id']}", json={"title": "Second"}
        )
        assert patched.json()["title"] == "Second" and patched.json()["achieved"]
        assert (
            await client.patch(f"{base}/manual-achievements/{item['id']}", json={"title": None})
        ).status_code == 422
        assert (
            await client.post(f"{base}/games/{game}/manual-achievements", json={"title": ""})
        ).status_code == 422
        assert (
            await client.post(
                f"{base}/games/{game}/manual-achievements", json={"title": "x", "bogus": 1}
            )
        ).status_code == 422
        assert (
            await client.post(
                f"{base}/games/{game}/manual-achievements",
                json={"title": "x", "icon_url": "ftp://x.test/i"},
            )
        ).status_code == 422
        view = (await client.get(f"{base}/games/{game}/tracking")).json()
        assert view["achievements"]["umbrella"]["unlocked"] == 1
        assert view["completion"]["summary"].startswith("Not completed")
        done = await client.put(f"{base}/games/{game}/completion", json={"status": "MASTERED"})
        assert done.json()["summary"] == "Completed"
        assert (
            await client.put(f"{base}/games/{game}/completion", json={"status": "nope"})
        ).status_code == 422
        assert (await client.get(f"{base}/games/{game}/completion/history")).json()[0][
            "status"
        ] == "MASTERED"

        # an interactive edit through the normal game form is also the user's choice
        patched = await client.patch(f"/api/game/update/{game}", json={"status": "PLAYING"})
        assert patched.status_code == 200
        assert (await client.get(f"{base}/games/{game}/completion")).json()["versions"][0][
            "user_set"
        ]

    async with client_for(user, "api_key") as client:  # a Playnite sync reports a different status
        pushed = await client.patch(
            f"/api/game/update/{game}", json={"status": "BACKLOG", "playtime_seconds": 60}
        )
        assert pushed.status_code == 200 and pushed.json()["status"] == "PLAYING"
        assert pushed.json()["playtime_seconds"] == 60
        await client.delete(f"{base}/games/{game}/completion")
        pushed = await client.patch(f"/api/game/update/{game}", json={"status": "BACKLOG"})
        assert pushed.json()["status"] == "BACKLOG"

    async with client_for(other) as client:
        assert (await client.get(f"{base}/games/{game}/tracking")).status_code == 404
        assert (
            await client.post(f"{base}/games/{game}/manual-achievements", json={"title": "x"})
        ).status_code == 404
        assert (
            await client.put(
                f"{base}/manual-achievements/{item['id']}/earned", json={"achieved": False}
            )
        ).status_code == 404
        assert (await client.delete(f"{base}/manual-achievements/{item['id']}")).status_code == 404
        assert (
            await client.get(f"{base}/manual-achievements/{item['id']}/icon")
        ).status_code == 404
        assert (
            await client.put(f"{base}/games/{game}/completion", json={"status": "BEATEN"})
        ).status_code == 404
    async with client_for(user) as client:
        assert (await client.delete(f"{base}/manual-achievements/{item['id']}")).status_code == 204
        assert (await client.get(f"{base}/games/{game}/achievements")).json()["umbrella"][
            "total"
        ] == 0


async def test_export_includes_manual_facts(users):  # noqa: F811
    user, other = await users(), await users()
    game = await make_game(user)
    made = await add(user, game, "Exported", achieved=True, icon_url="https://example.test/e.png")
    await run(manual.set_completion, user, game, GameStatus.BEATEN)
    async with SessionLocal() as db:
        snapshot = await build_library_export(db, user)
        empty = await build_library_export(db, other)
    assert [a["id"] for a in snapshot.manual_achievements] == [made.id]
    assert snapshot.manual_achievements[0]["icon_url"] == "https://example.test/e.png"
    assert snapshot.manual_completion[0]["status"] == "BEATEN"
    assert empty.manual_achievements == [] and empty.manual_completion == []


async def _manual_state(user):
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Game.title, Game.status, Game.locked_fields).where(Game.user_id == user)
            )
        ).all()
        found = (
            await db.execute(
                select(
                    Game.title,
                    GameManualAchievement.title,
                    GameManualAchievement.achieved,
                    GameManualAchievement.achieved_at,
                    GameManualAchievement.category,
                    GameManualAchievement.match_key,
                )
                .join(Game, Game.id == GameManualAchievement.game_id)
                .where(Game.user_id == user, GameManualAchievement.archived_at.is_(None))
            )
        ).all()
    return sorted(map(tuple, rows), key=str), sorted(map(tuple, found), key=str)


async def test_export_wipe_import_restores_identical_manual_state(users, monkeypatch):  # noqa: F811
    from src.api.routes import export_import

    monkeypatch.setattr(export_import, "create_game_folder", lambda *_a: None)
    user = await users()
    a, b = await make_game(user, "Game PC"), await make_game(user, "Game Deck")
    mine = await add(user, a, "Win", achieved=True, category="Story", match_key="win")
    await run(manual.set_earned, user, mine.id, True, 1_700_000_000)
    await add(user, b, "Win on Deck", match_key="win", description="d")
    await add(user, b, "Removed")
    gone = (await tracked(user, b))["manual"][-1]["id"]
    await run(manual.remove_achievement, user, gone)
    await run(manual.set_completion, user, a, GameStatus.BEATEN)
    await run(manual.set_completion, user, a, GameStatus.MASTERED)
    before = await _manual_state(user)

    async with client_for(user) as client:
        snapshot = (await client.get("/api/export/library")).json()
    assert len(snapshot["manual_achievements"]) == 2 and len(snapshot["manual_completion"]) == 2
    async with SessionLocal() as db:  # wipe
        await db.execute(Game.__table__.delete().where(Game.user_id == user))
        await db.commit()
    assert (await _manual_state(user)) == ([], [])

    async with client_for(user) as client:
        done = await client.post("/api/import/library", json=snapshot)
        assert done.status_code == 200 and done.json()["created"] == 2, done.text
        assert done.json()["manual_achievements"] == 2 and done.json()["manual_completion"] == 1
        assert done.json()["errors"] == []
    after = await _manual_state(user)
    assert after[1] == before[1]
    assert [(t, s) for t, s, _ in after[0]] == [(t, s) for t, s, _ in before[0]]
    assert all("status" in locked for t, s, locked in after[0] if t == "Game PC")
    async with client_for(user) as client:
        new_pc = next(
            g["id"] for g in (await client.get("/api/game/list")).json() if g["title"] == "Game PC"
        )
        view = (await client.get(f"/api/game-identity/games/{new_pc}/completion")).json()
        assert view["versions"][0]["status"] == "MASTERED" and view["versions"][0]["user_set"]

    # restoring the same file into the same library repeats games (as before) but each new
    # copy gets its rows once; the first copies keep theirs and nothing is shared
    async with client_for(user) as client:
        again = await client.post("/api/import/library", json=snapshot)
        assert again.json()["manual_achievements"] == 2
    assert len((await _manual_state(user))[1]) == 4


async def test_import_reports_bad_and_foreign_manual_rows_and_stays_owned(users, monkeypatch):  # noqa: F811
    from src.api.routes import export_import

    monkeypatch.setattr(export_import, "create_game_folder", lambda *_a: None)
    user, other = await users(), await users()
    foreign = await make_game(other, "Theirs")
    game_id = "11111111-1111-1111-1111-111111111111"
    body = {
        "games": [{"id": game_id, "title": "Mine", "folder_location": "mine-97"}],
        "manual_achievements": [
            {"game_id": game_id, "title": "ok", "achieved": True},
            {"game_id": game_id, "title": "ok", "achieved": True},  # duplicate in the file
            {"game_id": game_id, "title": "  "},
            {"game_id": game_id, "title": "x", "icon_url": "file:///etc/passwd"},
            {"game_id": game_id, "title": "x", "achieved_at": "soon", "achieved": True},
            {"game_id": str(foreign), "title": "steal"},
            {"game_id": None, "title": "umbrella", "scope": "umbrella"},
            "garbage",
        ],
        "manual_completion": [
            {"game_id": game_id, "status": "NOPE", "state": "current"},
            {"game_id": str(foreign), "status": "BEATEN", "state": "current"},
            {"game_id": game_id, "status": "BEATEN", "state": "superseded"},
            7,
        ],
    }
    async with client_for(user) as client:
        done = await client.post("/api/import/library", json=body)
    assert done.status_code == 200, done.text
    result = done.json()
    assert (result["manual_achievements"], result["manual_completion"]) == (1, 0)
    assert len(result["errors"]) >= 4
    state = await _manual_state(user)
    assert [r[1] for r in state[1]] == ["ok"]
    assert (await _manual_state(other))[1] == []


async def test_same_provider_achievement_id_in_two_apps_is_two_achievements(users):  # noqa: F811
    user = await users()
    a, b = await make_game(user, "Game"), await make_game(user, "Game Sequel")
    async with SessionLocal() as db:
        for game, app_id in ((a, "100"), (b, "200")):
            row_ = await db.get(Game, game)
            row_.external_id = app_id
        await db.commit()
    await seed(a, "Steam", row("ACH_1", "First", True))
    await seed(b, "Steam", row("ACH_1", "First"))
    await merge(user, b, a)
    view = await tracked(user, a)
    assert view["umbrella"]["total"] == 2 and view["umbrella"]["unlocked"] == 1

    async with SessionLocal() as db:  # the same app on both versions is one achievement
        (await db.get(Game, b)).external_id = "100"
        await db.commit()
    view = await tracked(user, a)
    assert view["umbrella"]["total"] == 1 and view["umbrella"]["unlocked"] == 1
