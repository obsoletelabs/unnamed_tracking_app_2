"""Configured account libraries share personal schedules without sharing identities."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from src.api.routes import jobs as job_routes
from src.api.routes import library_sync
from src.core.crypto import encrypt_secret
from src.database.models.achievement import Achievement
from src.database.models.game import Game, GameStatus
from src.database.models.job_setting import JobSetting
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import connection_jobs, jobs, plugin_jobs
from src.features.job_runs import job_lease
from src.helpers import save_game_asset


@pytest.fixture
async def connected(monkeypatch, tmp_path):
    actor_id, other_id = uuid4(), uuid4()
    transport = AsyncMock()
    transport.plugins.return_value = []
    monkeypatch.setattr(plugin_jobs, "client", transport)
    monkeypatch.setattr(connection_jobs, "_running", {})
    monkeypatch.setattr(connection_jobs, "_sync_owners", {})
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)
    async with SessionLocal() as db:
        for identity in (actor_id, other_id):
            db.add(
                User(
                    id=identity,
                    username=f"sync-{identity}",
                    email=f"{identity}@example.invalid",
                    password_hash="unused",
                    is_admin=False,
                    steam_id="76561190000000000",
                    steam_api_key="fixture-key",
                    psn_npsso_token=encrypt_secret("fixture-token"),
                    retroachievements_username="fixture",
                    retroachievements_api_key="fixture-key",
                )
            )
        await db.commit()
    yield actor_id, other_id
    tasks = list(connection_jobs._running.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_([actor_id, other_id])))
        await db.commit()


async def test_configured_connections_create_off_daily_tasks_scoped_to_each_owner(connected):
    actor_id, other_id = connected
    async with SessionLocal() as db:
        actor, other = await db.get(User, actor_id), await db.get(User, other_id)
        listed = await job_routes.list_user_jobs(db, actor)
        assert [task["name"] for task in listed] == [
            "Steam autosync",
            "PlayStation autosync",
            "RetroAchievements autosync",
        ]
        assert all(not task["enabled"] and task["interval_minutes"] == 1440 for task in listed)
        assert all(task["available"] and not task["plugin_id"] for task in listed)
        own_id = listed[0]["id"]
        assert own_id != connection_jobs.connection_job_id(other_id, "steam")
        with pytest.raises(HTTPException) as failure:
            await job_routes.run_user_job(own_id, db, other)
        assert failure.value.status_code == 404
        with pytest.raises(HTTPException) as failure:
            await job_routes.update_user_job(own_id, job_routes.JobUpdate(enabled=True), db, other)
        assert failure.value.status_code == 404
        with pytest.raises(HTTPException) as failure:
            await job_routes.run_job(own_id, db=db)
        assert failure.value.status_code == 404
        assert await jobs.get_scheduled_user_jobs(db) == []


async def test_partial_credentials_and_deferred_connections_do_not_create_jobs(connected):
    actor_id, _ = connected
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        actor.steam_api_key = actor.psn_npsso_token = actor.retroachievements_api_key = None
        actor.xbox_client_id, actor.gog_refresh_token = "saved", "saved"
        await db.commit()
        assert await connection_jobs.get_connection_jobs(db, actor_id) == []
        actor.steam_refresh_token = encrypt_secret("refresh")
        actor.steam_access_token = encrypt_secret("access")
        await db.commit()
        assert [spec.name for spec in await connection_jobs.get_connection_jobs(db, actor_id)] == [
            "Steam autosync"
        ]


async def test_disconnect_retains_history_and_interval_and_paused_task_can_be_disabled(connected):
    actor_id, _ = connected
    job_id = connection_jobs.connection_job_id(actor_id, "steam")
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        saved = await job_routes.update_user_job(
            job_id, job_routes.JobUpdate(enabled=True, interval_minutes=120), db, actor
        )
        assert saved["enabled"]
        row = await db.get(JobSetting, job_id)
        row.last_run_at, row.last_result = 123, {"summary": "Previous sync"}
        actor.steam_api_key = None
        await db.commit()
        listed = await job_routes.list_user_jobs(db, actor)
        paused = next(task for task in listed if task["id"] == job_id)
        assert paused["enabled"] and not paused["available"]
        assert paused["last_summary"] == "Previous sync"
        with pytest.raises(HTTPException) as failure:
            await job_routes.run_user_job(job_id, db, actor)
        assert failure.value.status_code == 409
        off = await job_routes.update_user_job(
            job_id, job_routes.JobUpdate(enabled=False), db, actor
        )
        assert not off["enabled"] and not off["available"]
        actor.steam_api_key = "new-key"
        await db.commit()
        restored = next(
            task for task in await job_routes.list_user_jobs(db, actor) if task["id"] == job_id
        )
        assert restored["available"] and not restored["enabled"]
        assert restored["interval_minutes"] == 120 and restored["last_run_at"] == 123


async def test_manual_run_ignores_switch_and_queued_schedule_rechecks_state(connected, monkeypatch):
    actor_id, _ = connected
    sync = AsyncMock(return_value={"games_added": 0})
    monkeypatch.setattr(connection_jobs, "_sync_library", sync)
    async with SessionLocal() as db:
        spec = (await connection_jobs.get_connection_jobs(db, actor_id))[0]
        await jobs.get_setting(db, spec)
        await db.commit()
        actor = await db.get(User, actor_id)
        assert (await job_routes.run_user_job(spec.id, db, actor))["running"]
    await asyncio.gather(*connection_jobs._running.values())
    sync.assert_awaited_once()
    async with SessionLocal() as db:
        row = await db.get(JobSetting, spec.id)
        assert not row.enabled and row.last_result["completed"]
    sync.reset_mock()
    spec.start("needed")
    await asyncio.gather(*connection_jobs._running.values())
    sync.assert_not_awaited()
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        actor.steam_api_key = None
        await db.commit()
    spec.start("all")
    await asyncio.gather(*connection_jobs._running.values())
    sync.assert_not_awaited()


async def test_runs_do_not_overlap_and_failure_does_not_expose_remote_secrets(
    connected, monkeypatch
):
    actor_id, _ = connected
    entered, release = asyncio.Event(), asyncio.Event()

    async def sync(*_args):
        entered.set()
        await release.wait()
        raise HTTPException(status_code=502, detail="private fixture-key fixture-token")

    worker = AsyncMock(side_effect=sync)
    monkeypatch.setattr(connection_jobs, "_sync_library", worker)
    async with SessionLocal() as db:
        spec = (await connection_jobs.get_connection_jobs(db, actor_id))[0]
    spec.start("all")
    await entered.wait()
    spec.start("all")
    assert worker.await_count == 1 and spec.is_running()
    release.set()
    await asyncio.gather(*connection_jobs._running.values())
    assert not spec.is_running()
    async with SessionLocal() as db:
        row = await db.get(JobSetting, spec.id)
        assert row.last_result["completed"] is False
        assert "fixture" not in row.last_result["summary"]
    worker.side_effect = None
    worker.return_value = {"games_added": 1, "games_updated": 2, "achievements_synced": 3}
    spec.start("all")
    await asyncio.gather(*connection_jobs._running.values())
    async with SessionLocal() as db:
        row = await db.get(JobSetting, spec.id)
        assert row.last_result["completed"] is True
        assert row.last_result["games_updated"] == 2


async def test_foreground_inventory_guard_blocks_same_connection_only(connected, monkeypatch):
    actor_id, other_id = connected
    implementation = AsyncMock(return_value={"games_added": 0})
    monkeypatch.setattr(library_sync, "_sync_steam_library", implementation)
    async with SessionLocal() as db:
        actor, other = await db.get(User, actor_id), await db.get(User, other_id)
        async with connection_jobs.sync_scope(actor_id, "steam"):
            with pytest.raises(HTTPException) as failure:
                await asyncio.create_task(library_sync.sync_steam_library(db, actor))
            assert failure.value.status_code == 409
            await asyncio.create_task(library_sync.sync_steam_library(db, other))
            async with connection_jobs.sync_scope(actor_id, "psn"):
                pass
        await library_sync.sync_steam_library(db, actor)
        assert implementation.await_count == 2


@pytest.mark.parametrize("provider", ["steam", "psn", "retroachievements"])
async def test_background_dispatch_reuses_real_import_and_preserves_manual_progress(
    connected, monkeypatch, provider
):
    actor_id, other_id = connected
    monkeypatch.setattr(library_sync, "_enrich_new_game", AsyncMock())
    monkeypatch.setattr(library_sync.steam, "resolve_steam_id", lambda sid, _key: sid)
    monkeypatch.setattr(
        library_sync.steam,
        "get_owned_games",
        Mock(return_value=[{"appid": 123, "name": "Fixture game", "playtime_forever": 20}]),
    )
    monkeypatch.setattr(library_sync.steam, "get_schema_for_game", Mock(return_value={}))
    monkeypatch.setattr(library_sync.steam, "get_player_achievements", Mock(return_value=[]))
    retro = Mock()
    retro.get_user_games.return_value = [{"GameID": 123, "Title": "Fixture game"}]
    retro.get_game_progress.return_value = {}
    monkeypatch.setattr(library_sync, "RetroAchievementsClient", Mock(return_value=retro))
    psn = Mock()
    psn.get_owned_titles.return_value = [
        {"npCommunicationId": "NP123", "trophyTitleName": "Fixture game"}
    ]
    psn.get_trophies_for_title.return_value = []
    monkeypatch.setattr(library_sync, "PSNClient", Mock(return_value=psn))
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        specs = await connection_jobs.get_connection_jobs(db, actor_id)
        spec = next(
            item
            for item in specs
            if item.id == connection_jobs.connection_job_id(actor_id, provider)
        )
        await job_routes.update_user_job(spec.id, job_routes.JobUpdate(enabled=True), db, actor)
        assert spec.id in [item.id for item in await jobs.get_scheduled_user_jobs(db)]
    spec.start("needed")
    await asyncio.gather(*connection_jobs._running.values())
    async with SessionLocal() as db:
        games = (await db.scalars(select(Game).where(Game.user_id == actor_id))).all()
        assert len(games) == 1
        game = games[0]
        game.status, game.notes, game.rating_overall = GameStatus.PLAYING, "Personal notes", 8
        await db.commit()
        identity = game.id
    spec.start("needed")
    await asyncio.gather(*connection_jobs._running.values())
    async with SessionLocal() as db:
        game = await db.get(Game, identity)
        assert (game.status, game.notes, game.rating_overall) == (
            GameStatus.PLAYING,
            "Personal notes",
            8,
        )
        assert len((await db.scalars(select(Game).where(Game.user_id == actor_id))).all()) == 1
        assert not (await db.scalars(select(Game).where(Game.user_id == other_id))).all()
        row = await db.get(JobSetting, spec.id)
        assert row.last_result["completed"] is True
        assert row.last_result["games_added"] == 0 and row.last_result["games_updated"] == 1


@pytest.mark.parametrize("provider", ["psn", "retroachievements"])
async def test_unavailable_achievement_snapshot_retains_saved_unlocks(
    connected, monkeypatch, provider
):
    actor_id, _ = connected
    label = connection_jobs.CONNECTIONS[provider]
    async with SessionLocal() as db:
        game = Game(
            user_id=actor_id,
            title="Fixture game",
            sort_title="fixture game",
            folder_location=uuid4().hex,
            source=label,
            external_id="NP123" if provider == "psn" else "123",
        )
        db.add(game)
        await db.flush()
        achievement = Achievement(
            game_id=game.id,
            provider=label,
            external_id="1",
            name="Saved unlock",
            unlocked=True,
            unlocked_at=123,
        )
        db.add(achievement)
        await db.commit()
        achievement_id = achievement.id
    retro = Mock()
    retro.get_user_games.return_value = [{"GameID": 123, "Title": "Fixture game"}]
    retro.get_game_progress.side_effect = library_sync.RetroAchievementsError("unavailable")
    monkeypatch.setattr(library_sync, "RetroAchievementsClient", Mock(return_value=retro))
    psn = Mock()
    psn.get_owned_titles.return_value = [
        {"npCommunicationId": "NP123", "trophyTitleName": "Fixture game"}
    ]
    psn.get_trophies_for_title.side_effect = library_sync.PSNError("unavailable")
    monkeypatch.setattr(library_sync, "PSNClient", Mock(return_value=psn))
    async with SessionLocal() as db:
        spec = next(
            item
            for item in await connection_jobs.get_connection_jobs(db, actor_id)
            if item.id == connection_jobs.connection_job_id(actor_id, provider)
        )
    spec.start("all")
    await asyncio.gather(*connection_jobs._running.values())
    async with SessionLocal() as db:
        saved = await db.get(Achievement, achievement_id)
        assert saved is not None and saved.unlocked and saved.unlocked_at == 123
        row = await db.get(JobSetting, spec.id)
        assert row.last_result["achievements_synced"] == 0
        assert row.last_result["achievement_games_unavailable"] == 1
        assert "saved achievements kept" in row.last_result["summary"]


async def test_disconnect_turns_the_schedule_off_without_touching_other_accounts(connected):
    actor_id, other_id = connected
    actor_job = connection_jobs.connection_job_id(actor_id, "steam")
    other_job = connection_jobs.connection_job_id(other_id, "steam")
    async with SessionLocal() as db:
        actor, other = await db.get(User, actor_id), await db.get(User, other_id)
        for owner, job_id in ((actor, actor_job), (other, other_job)):
            await job_routes.update_user_job(job_id, job_routes.JobUpdate(enabled=True), db, owner)
        actor.steam_api_key = None
        await db.commit()
        await jobs.disable_revoked_schedules(db)
        assert not (await db.get(JobSetting, actor_job)).enabled
        assert (await db.get(JobSetting, actor_job)).status == "disabled"
        assert (await db.get(JobSetting, other_job)).enabled
        scheduled = {spec.id for spec in await jobs.get_scheduled_user_jobs(db)}
        assert other_job in scheduled and actor_job not in scheduled


async def test_paused_connection_does_not_start_and_overlapping_runs_are_refused(
    connected, monkeypatch
):
    actor_id, _ = connected
    sync = AsyncMock(return_value={"games_added": 0})
    monkeypatch.setattr(connection_jobs, "_sync_library", sync)
    job_id = connection_jobs.connection_job_id(actor_id, "steam")
    async with SessionLocal() as db:
        actor = await db.get(User, actor_id)
        await job_routes.update_user_job(job_id, job_routes.JobUpdate(enabled=True), db, actor)
        await job_routes.control_user_job(job_id, "pause", db, actor)
        spec = next(
            s for s in await connection_jobs.get_connection_jobs(db, actor_id) if s.id == job_id
        )
        with pytest.raises(HTTPException) as refused:
            await job_routes.run_user_job(job_id, db, actor)
        assert refused.value.status_code == 409
    spec.start("needed")
    await asyncio.gather(*connection_jobs._running.values())
    sync.assert_not_awaited()
    async with SessionLocal() as db:
        await job_routes.control_user_job(job_id, "resume", db, await db.get(User, actor_id))
    async with job_lease(spec) as held:  # a second host process is mid-inventory
        assert held
        spec.start("all")
        await asyncio.gather(*connection_jobs._running.values())
        sync.assert_not_awaited()
    spec.start("all")
    await asyncio.gather(*connection_jobs._running.values())
    sync.assert_awaited_once()
    async with SessionLocal() as db:
        row = await db.get(JobSetting, job_id)
        assert row.status == "completed" and row.last_success_at and row.lease_token is None
