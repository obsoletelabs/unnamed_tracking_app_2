"""The full game editor saves its tracking fields with the game in one request."""

import pytest

from tests.test_game_files_flow import game_flow  # noqa: F401

# playtime_seconds is a PostgreSQL INTEGER column
PLAYTIME_LIMIT = 2_147_483_647


async def test_create_saves_tracking_fields_with_the_game(flow):
    created = await flow.client.post(
        "/api/game/create",
        json={
            "title": "Tracked",
            "folder_location": "Tracked",
            "favorite": True,
            "collections": ["Couch", "Backlog 2026"],
            "resume_note": "Chapter 3 boss",
            "playtime_seconds": 5400,
        },
    )
    assert created.status_code == 201, created.text
    saved = (await flow.client.get(f"/api/game/get/{created.json()['id']}")).json()
    assert saved["favorite"] is True
    assert saved["collections"] == ["Couch", "Backlog 2026"]
    assert saved["resume_note"] == "Chapter 3 boss"
    assert saved["playtime_seconds"] == 5400


async def test_editor_update_leaves_an_omitted_playtime_alone(flow):
    synced = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={"playtime_seconds": 7261}
    )
    assert synced.status_code == 200, synced.text
    edited = await flow.client.patch(
        f"/api/game/update/{flow.game_id}",
        json={"favorite": True, "collections": ["Couch"], "resume_note": "Boss rush"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["playtime_seconds"] == 7261
    assert edited.json()["resume_note"] == "Boss rush"
    cleared = await flow.client.patch(
        f"/api/game/update/{flow.game_id}",
        json={"resume_note": None, "collections": [], "playtime_seconds": 0},
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert (body["resume_note"], body["collections"], body["playtime_seconds"]) == (None, [], 0)
    assert body["favorite"] is True


@pytest.mark.parametrize("seconds", [PLAYTIME_LIMIT + 1, -1])
async def test_playtime_outside_the_stored_range_is_a_validation_error(flow, seconds):
    created = await flow.client.post(
        "/api/game/create",
        json={"title": "Too long", "folder_location": "Too-long", "playtime_seconds": seconds},
    )
    assert created.status_code == 422, created.text
    edited = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={"playtime_seconds": seconds}
    )
    assert edited.status_code == 422, edited.text
    games = (await flow.client.get("/api/game/list")).json()
    assert [game["title"] for game in games] == ["Flow"]
    assert games[0]["playtime_seconds"] == 0


async def test_largest_stored_playtime_is_accepted(flow):
    edited = await flow.client.patch(
        f"/api/game/update/{flow.game_id}", json={"playtime_seconds": PLAYTIME_LIMIT}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["playtime_seconds"] == PLAYTIME_LIMIT
