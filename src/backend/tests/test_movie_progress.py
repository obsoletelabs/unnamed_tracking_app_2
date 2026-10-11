"""Movie progress remains integer minutes and rejects database overflow at the API."""

import pytest

from tests.test_game_files_flow import game_flow  # noqa: F401 - register the flow fixture


@pytest.mark.parametrize("minutes", [-1, 2_147_483_648])
async def test_movie_progress_rejects_invalid_create_and_update(flow, minutes):
    rejected = await flow.client.post(
        "/api/movie/create", json={"title": "Invalid position", "progress_minutes": minutes}
    )
    assert rejected.status_code == 422, rejected.text
    assert (await flow.client.get("/api/movie/list")).json()["total"] == 0

    created = await flow.client.post(
        "/api/movie/create",
        json={"title": "Preserved position", "progress_minutes": 72, "note": "Keep this note"},
    )
    assert created.status_code == 201, created.text
    movie = created.json()
    rejected = await flow.client.patch(
        f"/api/movie/update/{movie['id']}",
        json={"progress_minutes": minutes, "note": "Must not be saved"},
    )
    assert rejected.status_code == 422, rejected.text
    saved = (await flow.client.get(f"/api/movie/get/{movie['id']}")).json()
    assert saved["progress_minutes"] == 72
    assert saved["note"] == "Keep this note"


@pytest.mark.parametrize("minutes", [0, 2_147_483_647])
async def test_movie_progress_preserves_storable_boundaries_and_can_clear(flow, minutes):
    response = await flow.client.post(
        "/api/movie/create", json={"title": "Progress boundary", "progress_minutes": minutes}
    )
    assert response.status_code == 201, response.text
    movie = response.json()
    assert movie["progress_minutes"] == minutes
    response = await flow.client.patch(
        f"/api/movie/update/{movie['id']}", json={"progress_minutes": minutes}
    )
    assert response.status_code == 200, response.text
    assert response.json()["progress_minutes"] == minutes
    response = await flow.client.patch(
        f"/api/movie/update/{movie['id']}", json={"progress_minutes": None}
    )
    assert response.status_code == 200, response.text
    assert response.json()["progress_minutes"] is None
