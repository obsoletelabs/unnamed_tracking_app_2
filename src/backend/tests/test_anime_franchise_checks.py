"""Completed anime franchise checks: opt-in types, franchise identity, bounded resumable runs."""

import time
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from src.api.routes import jobs as job_routes
from src.core.preferences import DEFAULTS, load_preferences, save_preferences
from src.database.models.anime import Anime, AnimeStatus
from src.database.models.job_setting import JobSetting
from src.database.models.notification import Notification
from src.database.models.notification_destination import NotificationDestination
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import anime_franchise, jobs, plugin_jobs
from src.features.metadata.builtin.anilist_franchise import FranchiseGraph
from src.features.metadata.core_http import ProviderFailure
from src.features.notification_policy import INBOX_PROVIDER
from src.features.notification_settings import routing_settings

SEASON_TYPE, EXTRA_TYPE = "media.franchise.season", "media.franchise.extra"


def media(entry_id, title, media_format="TV", **relations):
    """One AniList Media node with PREQUEL/SEQUEL/SIDE_STORY edges to other nodes."""
    edges = [
        {"relationType": relation, "node": {"id": target, "format": "TV"}}
        for relation, targets in relations.items()
        for target in targets
    ]
    return {
        "id": entry_id,
        "title": {"english": title},
        "format": media_format,
        "relations": {"edges": edges},
    }


class AniListFake:
    """The franchise transport, served from fixtures: the real gateway and traversal run."""

    def __init__(self):
        self.nodes: dict[int, dict] = {}
        self.requests: list[int] = []
        self.failing = False

    def add(self, *nodes):
        for node in nodes:
            self.nodes[node["id"]] = node

    def link(self):
        """Give every edge the target's real format, as AniList does."""
        for node in self.nodes.values():
            for edge in node["relations"]["edges"]:
                target = self.nodes.get(edge["node"]["id"])
                if target:
                    edge["node"] = {key: target[key] for key in ("id", "title", "format")}

    async def post(self, _graph, _query, variables):
        if self.failing:
            raise ProviderFailure("unavailable")
        self.requests.append(variables.get("id"))
        self.link()
        return {"data": {"Media": self.nodes.get(variables.get("id"))}}


@pytest.fixture
async def anilist(monkeypatch):
    fake = AniListFake()

    async def post(graph, query, variables):
        return await fake.post(graph, query, variables)

    monkeypatch.setattr(FranchiseGraph, "_post_graphql", post)
    monkeypatch.setattr(anime_franchise, "relations_inflight", set())
    return fake


@pytest.fixture
async def accounts():
    """Two accounts in id order; the shared franchise job row is reset around each test."""
    identities = sorted([uuid4(), uuid4()])
    async with SessionLocal() as db:
        await db.execute(delete(JobSetting).where(JobSetting.job_id == "anime_franchise_check"))
        for identity in identities:
            db.add(
                User(
                    id=identity,
                    username=f"franchise_{identity.hex}",
                    email=f"{identity}@example.test",
                    password_hash="unused",
                )
            )
        await db.commit()
    try:
        yield identities
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id.in_(identities)))
            await db.execute(delete(JobSetting).where(JobSetting.job_id == "anime_franchise_check"))
            await db.commit()


async def add_title(user_id, anilist_id, title, status=AnimeStatus.WATCHED) -> UUID:
    async with SessionLocal() as db:
        show = Anime(
            user_id=user_id,
            title=title,
            sort_title=title.lower(),
            status=status,
            anilist_id=str(anilist_id),
            studios=[],
            countries=[],
            languages=[],
            genres=[],
            tags=[],
            features=[],
        )
        db.add(show)
        await db.commit()
        return show.id


async def opt_in(user_id, **choices):
    async with SessionLocal() as db:
        await save_preferences(db, user_id, choices)


async def refresh(anime_id, *, scheduled=False) -> int:
    async with SessionLocal() as db:
        show = await db.get(Anime, anime_id)
        _graph, created = await anime_franchise.fetch_and_store_relations(
            db, show, scheduled=scheduled
        )
        return created


async def notices(user_id) -> list[Notification]:
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.event_at, Notification.body)
            )
        )


async def run_batch() -> dict:
    """One scheduled batch through the registry, persisting its run like the loop does."""
    async with SessionLocal() as db:
        previous = dict(
            (await jobs.get_setting(db, jobs.JOBS["anime_franchise_check"])).last_result or {}
        )
        await db.commit()
    result = await anime_franchise.run_franchise_check(previous)
    await jobs.record_run("anime_franchise_check", result)
    return result


def franchise(anilist):
    """Season 1 (10) → Season 2 (11); later fixtures list Season 3 (12) and extras."""
    anilist.add(
        media(10, "Season 1", SEQUEL=[11]),
        media(11, "Season 2", PREQUEL=[10]),
    )


def announce_extras(anilist):
    """AniList lists a third season, a sequel movie, a side-story OVA and a source manga."""
    anilist.add(
        media(11, "Season 2", PREQUEL=[10], SEQUEL=[12], SIDE_STORY=[30, 40]),
        media(12, "Season 3", "TV", PREQUEL=[11], SEQUEL=[20]),
        media(20, "The Movie", "MOVIE", PREQUEL=[12]),
        media(30, "Hot Springs OVA", "OVA"),
        media(40, "Source manga", "MANGA"),
    )


async def test_new_and_existing_accounts_start_with_franchise_checks_off(accounts):
    owner, _ = accounts
    assert DEFAULTS["notify_franchise_season"] is False
    assert DEFAULTS["notify_franchise_extra"] is False
    async with SessionLocal() as db:
        # An existing account with saved choices but no franchise keys stays opted out.
        await save_preferences(db, owner, {"notify_sequel_announced": True})
        prefs = await load_preferences(db, owner)
        assert not anime_franchise.franchise_checks_enabled(prefs)
        settings = await routing_settings(db, owner)
    types = {item["event_type"]: item for item in settings["types"]}
    assert types[SEASON_TYPE]["preference_key"] == "notify_franchise_season"
    assert types[EXTRA_TYPE]["preference_key"] == "notify_franchise_extra"
    assert types[SEASON_TYPE]["label"] != types[EXTRA_TYPE]["label"]
    assert types[SEASON_TYPE]["required_trust"] == "PRIVATE"
    assert jobs.JOBS["anime_franchise_check"].default_enabled is True


async def test_opening_a_title_only_records_a_baseline_on_first_fetch(accounts, anilist):
    owner, _ = accounts
    franchise(anilist)
    announce_extras(anilist)
    await opt_in(owner, notify_franchise_season=True, notify_franchise_extra=True)
    first = await add_title(owner, 10, "Season 1")
    assert await refresh(first) == 0
    assert await notices(owner) == []
    async with SessionLocal() as db:
        stored = (await db.get(Anime, first)).relations_cache
    # The sequence is complete; side stories belong to the fetched title (Season 1 has none).
    assert set(stored["known_ids"]) == {10, 11, 12, 20}


async def test_seasons_and_extras_are_separate_types_and_skip_library_titles(accounts, anilist):
    owner, _ = accounts
    franchise(anilist)
    first = await add_title(owner, 10, "Season 1")
    second = await add_title(owner, 11, "Season 2")
    assert await refresh(second) == 0
    announce_extras(anilist)
    await add_title(owner, 12, "Season 3", AnimeStatus.WATCHLIST)
    await opt_in(owner, notify_franchise_season=True, notify_franchise_extra=True)

    assert await refresh(second) == 2
    rows = await notices(owner)
    assert {(row.kind, row.event_type, row.body) for row in rows} == {
        ("franchise_extra", EXTRA_TYPE, "A new movie is listed: The Movie"),
        ("franchise_extra", EXTRA_TYPE, "A new OVA is listed: Hot Springs OVA"),
    }
    assert all(row.media_id == second and row.title == "Season 2" for row in rows)
    assert all(row.dedupe_key.startswith("anime-franchise:") for row in rows)
    # Season 3 is already in the library and the source manga is not an anime extra.
    # The sibling Season 1 shares the stored baseline, so it never re-announces them.
    assert await refresh(first) == 0
    assert len(await notices(owner)) == 2


async def test_season_type_replaces_sequel_announcement_and_extras_need_their_own_opt_in(
    accounts, anilist
):
    owner, _ = accounts
    franchise(anilist)
    second = await add_title(owner, 11, "Season 2")
    await refresh(second)
    announce_extras(anilist)
    await opt_in(owner, notify_franchise_season=True)

    assert await refresh(second) == 1
    (row,) = await notices(owner)
    assert (row.kind, row.event_type) == ("franchise_season", SEASON_TYPE)
    assert row.body == "A new season is listed: Season 3"


async def test_without_opt_in_the_existing_sequel_announcement_is_preserved(accounts, anilist):
    owner, _ = accounts
    franchise(anilist)
    second = await add_title(owner, 11, "Season 2")
    await refresh(second)
    announce_extras(anilist)

    assert await refresh(second) == 1
    (row,) = await notices(owner)
    assert (row.kind, row.event_type) == ("sequel_announced", "media.sequel.announced")


async def test_unfinished_filtered_or_trashed_titles_send_nothing(accounts, anilist):
    owner, other = accounts
    unfinished = await add_title(owner, 11, "Season 2", AnimeStatus.IN_PROGRESS)
    franchise(anilist)
    await refresh(unfinished)
    announce_extras(anilist)
    await opt_in(owner, notify_franchise_season=True, notify_franchise_extra=True)
    assert await refresh(unfinished) == 0

    filtered = await add_title(other, 11, "Season 2")
    franchise(anilist)
    await refresh(filtered)
    baseline = {"chain": [{"id": 10}, {"id": 11}], "branches": [], "version": 2}
    announce_extras(anilist)
    await opt_in(
        other,
        notify_franchise_season=True,
        notify_franchise_extra=True,
        notify_media_types=["tv", "movie"],
    )
    assert await refresh(filtered) == 0

    async with SessionLocal() as db:
        show = await db.get(Anime, filtered)
        show.relations_cache, show.deleted_at = baseline, int(time.time())
        await db.commit()
    await opt_in(other, notify_media_types=["anime", "tv", "movie"])
    assert await refresh(filtered) == 0
    assert await notices(owner) == [] and await notices(other) == []


async def test_scheduled_batches_are_bounded_resumable_and_deduplicated(
    accounts, anilist, monkeypatch
):
    owner, other = accounts
    monkeypatch.setattr(anime_franchise, "MAX_FRANCHISES_PER_RUN", 2)
    for base in (100, 200, 300):
        anilist.add(media(base, f"Show {base}", SEQUEL=[base + 1]))
        anilist.add(media(base + 1, f"Show {base} S2", PREQUEL=[base]))
    for account in accounts:
        await opt_in(account, notify_franchise_season=True)
    # Both seasons of franchise 100 are completed: one franchise, one fetch.
    await add_title(owner, 100, "Show 100")
    await add_title(owner, 101, "Show 100 S2")
    await add_title(owner, 200, "Show 200")
    await add_title(other, 300, "Show 300")
    # A disabled account is never visited, even when it opted in.
    async with SessionLocal() as db:
        dormant = User(
            id=uuid4(),
            username=f"dormant_{uuid4().hex}",
            email=f"{uuid4()}@example.test",
            password_hash="unused",
            is_active=False,
        )
        db.add(dormant)
        await db.commit()
    try:
        await opt_in(dormant.id, notify_franchise_season=True)
        await add_title(dormant.id, 100, "Dormant copy")

        first = await run_batch()
        assert first["completed"] is False and first["checked"] == 2
        assert first["continue_after_seconds"] == anime_franchise.CONTINUE_AFTER_SECONDS
        # The owner's two franchises used the budget; the next account is the cursor.
        assert first["cursor_user"] == str(other) and first["cursor_franchise"] == 0
        assert "Continues in 5 minutes" in first["summary"]
        # A restart keeps no in-memory state; the next batch reads only the persisted
        # cursor and resumes with the next account instead of starting over.
        monkeypatch.setattr(anime_franchise, "relations_inflight", set())
        async with SessionLocal() as db:
            saved = (await db.get(JobSetting, "anime_franchise_check")).last_result
        assert saved["cursor_user"] == str(other) and saved["completed"] is False
        second = await run_batch()
        assert second["completed"] is True and second["checked"] == 1
        assert "Every opted-in account was checked" in second["summary"]
        assert set(anilist.requests) == {100, 101, 200, 201, 300, 301}
        # Both completed seasons of franchise 100 cost one traversal, not one per title.
        assert anilist.requests.count(100) == 1 and anilist.requests.count(101) == 1

        # The franchise grows; a new pass announces it once to its owner only.
        anilist.add(media(101, "Show 100 S2", PREQUEL=[100], SEQUEL=[102]))
        anilist.add(media(102, "Show 100 S3", PREQUEL=[101]))
        await run_batch()
        await run_batch()
        owned = await notices(owner)
        assert [(row.kind, row.body) for row in owned] == [
            ("franchise_season", "A new season is listed: Show 100 S3")
        ]
        assert owned[0].title == "Show 100 S2"
        assert await notices(other) == []
        assert await notices(dormant.id) == []
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == dormant.id))
            await db.commit()


async def test_scheduled_check_defers_seasons_to_sequel_announcements_without_opt_in(
    accounts, anilist
):
    owner, _ = accounts
    franchise(anilist)
    second = await add_title(owner, 11, "Season 2")
    await refresh(second)
    announce_extras(anilist)
    await opt_in(owner, notify_franchise_extra=True)

    result = await run_batch()
    assert result["completed"] is True and result["notified"] == 2
    assert {row.kind for row in await notices(owner)} == {"franchise_extra"}
    # The scheduled check sent no season notice and kept the old graph, so opening the
    # title still produces the account's existing sequel announcement exactly once.
    assert await refresh(second) == 1
    kinds = sorted(row.kind for row in await notices(owner))
    assert kinds == ["franchise_extra", "franchise_extra", "sequel_announced"]
    assert await refresh(second) == 0


async def test_provider_failures_back_off_and_a_stuck_franchise_cannot_block_others(
    accounts, anilist
):
    owner, other = accounts
    for account, base in ((owner, 100), (other, 200)):
        anilist.add(media(base, f"Show {base}"))
        await add_title(account, base, f"Show {base}")
        await opt_in(account, notify_franchise_extra=True)
    anilist.failing = True
    delays = []
    for _attempt in range(2):
        result = await run_batch()
        assert result["completed"] is False and result["failed"] == 1
        assert result["error"] == "unavailable" and result["checked"] == 0
        assert result["cursor_user"] == str(owner) and result["cursor_franchise"] == 0
        delays.append(result["continue_after_seconds"])
    assert delays == [300, 600]
    async with SessionLocal() as db:
        row = await db.get(JobSetting, "anime_franchise_check")
        assert row.last_run_at is not None and "unavailable" in row.last_result["summary"]

    # The third failure skips the stuck franchise and moves to the next account.
    original = anilist.post

    async def owner_franchise_fails(graph, query, variables):
        if variables.get("id") == 100:
            raise ProviderFailure("unavailable")
        return await original(graph, query, variables)

    anilist.failing = False
    anilist.post = owner_franchise_fails
    result = await run_batch()
    assert result["completed"] is True
    assert (result["failed"], result["skipped"], result["checked"]) == (1, 1, 1)
    assert "skipped until the next pass" in result["summary"]
    assert 200 in anilist.requests


async def test_franchise_task_is_an_admin_job_not_a_personal_task(accounts, monkeypatch):
    owner, _ = accounts
    runtime = AsyncMock()
    runtime.plugins.return_value = []
    monkeypatch.setattr(plugin_jobs, "client", runtime)
    async with SessionLocal() as db:
        listed = {job["id"]: job for job in await job_routes.list_jobs(db)}
        assert listed["anime_franchise_check"]["enabled"] is True
        assert listed["anime_franchise_check"]["interval_minutes"] == 24 * 60
        user = await db.get(User, owner)
        personal = await job_routes.list_user_jobs(db, user)
        assert "anime_franchise_check" not in {job["id"] for job in personal}
        with pytest.raises(HTTPException) as missing:
            await job_routes.run_user_job("anime_franchise_check", db, user)
        assert missing.value.status_code == 404


async def test_opted_in_account_route_and_inbox_follow_existing_routing(accounts, anilist):
    owner, _ = accounts
    franchise(anilist)
    second = await add_title(owner, 11, "Season 2")
    await refresh(second)
    announce_extras(anilist)
    async with SessionLocal() as db:
        await routing_settings(db, owner)
        inbox = await db.scalar(
            select(NotificationDestination).where(
                NotificationDestination.user_id == owner,
                NotificationDestination.provider_id == INBOX_PROVIDER,
            )
        )
    await opt_in(
        owner,
        notify_franchise_season=True,
        notify_franchise_extra=True,
        notification_routes={SEASON_TYPE: {str(inbox.id): {"enabled": False, "urgency": "normal"}}},
    )
    await refresh(second)
    by_kind = {row.kind: row for row in await notices(owner)}
    assert not by_kind["franchise_season"].inbox_visible
    assert by_kind["franchise_extra"].inbox_visible


async def test_relations_route_stays_owner_scoped_and_inbox_groups_new_types(accounts, anilist):
    import httpx

    from src.core.auth import get_current_user
    from src.main import app

    owner, other = accounts
    franchise(anilist)
    second = await add_title(owner, 11, "Season 2")
    await opt_in(owner, notify_franchise_season=True, notify_franchise_extra=True)
    async with SessionLocal() as db:
        users = {identity: await db.get(User, identity) for identity in accounts}
    current = {"user": users[owner]}
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = await client.get(f"/api/anime/{second}/relations")
            assert first.status_code == 200
            assert [entry["id"] for entry in first.json()["chain"]] == [10, 11]
            assert "known_ids" not in first.json()
            current["user"] = users[other]
            assert (await client.get(f"/api/anime/{second}/relations")).status_code == 404
            current["user"] = users[owner]
            announce_extras(anilist)
            assert await refresh(second) == 3
            seasons = (await client.get("/api/notifications?category=seasons")).json()
            releases = (await client.get("/api/notifications?category=releases")).json()
            assert [item["kind"] for item in seasons["items"]] == ["franchise_season"]
            assert sorted(item["kind"] for item in releases["items"]) == [
                "franchise_extra",
                "franchise_extra",
            ]
            current["user"] = users[other]
            assert (await client.get("/api/notifications")).json()["total"] == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)
