"""Object identifiers carried in request bodies and queries, not only in the path.

The path sweep cannot see these, so each is exercised with a victim's identifier.
"""

import httpx
import pytest
from sqlalchemy import delete, select

from src.database.models.anime import AnimeEpisode
from src.database.models.game import Game
from src.database.models.media_extras import MediaList, MediaListItem
from src.database.models.movies import Movie
from src.database.models.tv_show import TVEpisode
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app
from tests.route_audit_support import create_tenants


@pytest.fixture
async def world():
    tenants = await create_tenants()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        tenants.client = client
        yield tenants
    async with SessionLocal() as db:
        await db.execute(
            delete(User).where(
                User.id.in_(
                    [tenants.admin.user.id, tenants.attacker.user.id, tenants.victim.user.id]
                )
            )
        )
        await db.commit()


async def test_bulk_update_ignores_another_users_game_ids(world):
    response = await world.client.patch(
        "/api/game/bulk-update",
        headers=world.attacker.headers,
        json={
            "game_ids": [world.victim.ids["game_id"], world.attacker.ids["game_id"]],
            "developer": "Hijacked",
        },
    )
    assert response.status_code == 200 and response.json() == {"updated": 1}
    async with SessionLocal() as db:
        assert (await db.get(Game, world.victim.ids["game_id"])).developer != "Hijacked"


@pytest.mark.parametrize("route", ["/api/game/create"])
async def test_parent_game_must_be_the_callers(world, route):
    created = await world.client.post(
        route,
        headers=world.attacker.headers,
        json={
            "title": "Child",
            "folder_location": "child-audit",
            "parent_game_id": world.victim.ids["game_id"],
            "relationship_type": "dlc",
        },
    )
    assert created.status_code in (400, 404, 422), created.text
    updated = await world.client.patch(
        f"/api/game/update/{world.attacker.ids['game_id']}",
        headers=world.attacker.headers,
        json={
            "parent_game_id": world.victim.ids["game_id"],
            "relationship_type": "dlc",
        },
    )
    assert updated.status_code in (400, 404, 422), updated.text


async def test_list_cannot_adopt_another_users_media_or_lists(world):
    attacker_list = world.attacker.ids["list_id"]
    response = await world.client.post(
        f"/api/lists/{attacker_list}/items",
        headers=world.attacker.headers,
        json={"media_type": "movie", "media_id": world.victim.ids["movie_id"]},
    )
    assert response.status_code == 404
    async with SessionLocal() as db:
        adopted = await db.scalar(
            select(MediaListItem).where(
                MediaListItem.list_id == attacker_list,
                MediaListItem.media_id == world.victim.ids["movie_id"],
            )
        )
        assert adopted is None
    reorder = await world.client.put(
        f"/api/lists/{attacker_list}/order",
        headers=world.attacker.headers,
        json={"item_ids": [world.victim.ids["list_item_id"]]},
    )
    assert reorder.status_code in (204, 400, 404, 422)
    async with SessionLocal() as db:
        item = await db.get(MediaListItem, world.victim.ids["list_item_id"])
        assert str(item.list_id) == world.victim.ids["list_id"] and item.position == 0
    detail = await world.client.get(
        f"/api/lists/{world.victim.ids['list_id']}", headers=world.attacker.headers
    )
    assert detail.status_code == 404


async def test_activity_and_rewatch_logs_require_the_callers_media(world):
    for route, payload in (
        (
            "/api/rewatches",
            {
                "media_type": "movie",
                "media_id": world.victim.ids["movie_id"],
                "finished_on": "2024-02-02",
            },
        ),
        (
            "/api/activity",
            {
                "media_type": "movie",
                "media_id": world.victim.ids["movie_id"],
                "event_type": "finished",
                "event_date": "2024-02-02",
            },
        ),
    ):
        response = await world.client.post(route, headers=world.attacker.headers, json=payload)
        assert response.status_code in (400, 404, 422), (route, response.text)


async def test_episode_bulk_watch_only_touches_the_shows_own_episodes(world):
    for kind, episode_key, season_key, show_key, model in (
        ("tv", "tv_episode_id", "tv_season_id", "tv_id", TVEpisode),
        ("anime", "anime_episode_id", "anime_season_id", "anime_id", AnimeEpisode),
    ):
        response = await world.client.patch(
            f"/api/{kind}/{world.attacker.ids[show_key]}/seasons/{world.attacker.ids[season_key]}"
            "/episodes/bulk-watched",
            headers=world.attacker.headers,
            json={"episode_ids": [world.victim.ids[episode_key]], "watched": True},
        )
        assert response.status_code in (200, 404, 422)
        async with SessionLocal() as db:
            episode = await db.get(model, world.victim.ids[episode_key])
            assert not episode.watched


async def test_profile_picture_and_account_updates_are_self_only(world):
    victim = world.victim.ids["user_id"]
    other = await world.client.get(
        f"/api/user/{victim}/profile-picture", headers=world.attacker.headers
    )
    assert other.status_code == 403
    promoted = await world.client.patch(
        "/api/auth/me", headers=world.attacker.headers, json={"is_admin": True}
    )
    assert promoted.status_code in (200, 422)
    async with SessionLocal() as db:
        assert not (await db.get(User, world.attacker.user.id)).is_admin
    assert (
        await world.client.patch(
            f"/api/auth/users/{world.attacker.user.id}",
            headers=world.attacker.headers,
            json={"is_admin": True},
        )
    ).status_code == 403


async def test_api_keys_cannot_mint_keys_or_revoke_another_users(world):
    minted = await world.client.post(
        "/api/auth/api-keys", headers=world.attacker.headers, json={"name": "escalate"}
    )
    assert minted.status_code == 403
    revoked = await world.client.delete(
        f"/api/auth/api-keys/{world.victim.ids['key_id']}", headers=world.attacker.headers
    )
    assert revoked.status_code == 404
    listing = await world.client.get("/api/auth/api-keys", headers=world.victim.headers)
    assert listing.status_code == 200
    assert (await world.client.get("/api/auth/me", headers=world.victim.headers)).status_code == 200


async def test_movie_counts_are_not_cross_tenant(world):
    listing = await world.client.get("/api/movie/list", headers=world.attacker.headers)
    assert listing.status_code == 200
    body = listing.json()
    items = body["items"] if isinstance(body, dict) else body
    ids = {item["id"] for item in items}
    assert world.victim.ids["movie_id"] not in ids and world.attacker.ids["movie_id"] in ids
    async with SessionLocal() as db:
        assert await db.get(Movie, world.victim.ids["movie_id"]) is not None
        assert await db.scalar(
            select(MediaList.id).where(MediaList.id == world.victim.ids["list_id"])
        )


async def test_legacy_key_scopes_are_labels_and_never_widen_to_administration(world):
    """Legacy keys keep their owner's full authority (documented), but no more than that."""
    created = await world.client.post(
        "/api/auth/api-keys",
        headers={},
        json={"name": "legacy", "scopes": ["games.read"]},
    )
    assert created.status_code == 401
    # A restricted-looking label still edits the owner's own game, as before the audit.
    from src.core.auth import create_api_key
    from src.database.models.auth import UserApiKey

    raw, prefix, digest = create_api_key()
    async with SessionLocal() as db:
        db.add(
            UserApiKey(
                user_id=world.attacker.user.id,
                name="labelled",
                key_prefix=prefix,
                key_hash=digest,
                scopes=["games.read"],
            )
        )
        await db.commit()
    headers = {"Authorization": "Bearer " + raw}
    own = await world.client.patch(
        f"/api/game/update/{world.attacker.ids['game_id']}",
        headers=headers,
        json={"favorite": True},
    )
    assert own.status_code == 200
    assert (await world.client.get("/api/auth/users", headers=headers)).status_code == 403
    assert (
        await world.client.patch(
            f"/api/game/update/{world.victim.ids['game_id']}",
            headers=headers,
            json={"favorite": True},
        )
    ).status_code == 404
    # An administrator's key reaches administrator routes only through the admin scope.
    assert (
        await world.client.get("/api/auth/users", headers=world.admin.headers)
    ).status_code == 200
