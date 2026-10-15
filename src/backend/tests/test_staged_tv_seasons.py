"""Selected lookup seasons are staged until Save and never rewrite existing tracking."""

from tests.test_game_files_flow import game_flow  # noqa: F401


async def test_save_adds_only_new_selected_seasons_idempotently(flow):
    response = await flow.client.post(
        "/api/tv/create",
        json={
            "title": "Staged show",
            "seasons": [{"season_number": 1, "name": "Manual season", "episode_count": 8}],
        },
    )
    assert response.status_code == 201, response.text
    show = response.json()
    original = show["seasons"][0]
    payload = {
        "description": "Checked text",
        "seasons": [
            {"season_number": 1, "name": "Provider overwrite", "episode_count": 20},
            {"season_number": 2, "name": "New season", "episode_count": 10},
            {"season_number": 2, "name": "New season", "episode_count": 10},
        ],
    }
    for _ in range(2):
        response = await flow.client.patch(f"/api/tv/update/{show['id']}", json=payload)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert len(saved["seasons"]) == 2
        assert saved["seasons"][0]["id"] == original["id"]
        assert saved["seasons"][0]["name"] == "Manual season"
        assert saved["seasons"][0]["episode_count"] == 8
        assert saved["seasons"][1]["episodes_watched"] == 0
        assert saved["external_id"] is None and saved["provider_ids"] == {}
