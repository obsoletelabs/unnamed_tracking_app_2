"""Core search works without plugins while retaining the improved public behavior."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from test_metadata_provider_configuration import provider_boundary  # noqa: F401
from test_plugin_authorization_http import boundary  # noqa: F401

from src.core.auth import session_cookie_name
from src.core.crypto import encrypt_secret
from src.core.provider_credentials import _ENVIRONMENT_FALLBACKS
from src.database.models.app_integration_settings import AppIntegrationSettings
from src.database.models.plugin_metadata_provider import (
    PluginMetadataProviderRegistration,
    PluginProviderConfiguration,
)
from src.features.metadata import core, core_http, providers
from src.features.metadata.builtin import anilist, anizip, omdb, tvmaze
from src.features.metadata.builtin.anilist_franchise import FranchiseGraph
from src.features.metadata.configuration import save_configuration
from src.plugin_api.metadata_contracts import MediaType, MetadataProviderRequest


@pytest.fixture
def native_boundary(provider_boundary, monkeypatch):  # noqa: F811 - imported pytest fixture
    item = provider_boundary
    AppIntegrationSettings.__table__.create(item.session.bind)
    item.session.add(AppIntegrationSettings())
    item.session.commit()
    for key in _ENVIRONMENT_FALLBACKS:
        monkeypatch.setitem(_ENVIRONMENT_FALLBACKS, key, None)
    monkeypatch.setattr(providers, "discover_core_providers", core.discover_core_providers)

    @asynccontextmanager
    async def session():
        yield item.database

    monkeypatch.setattr(core, "SessionLocal", session)
    return item


@pytest.mark.asyncio
async def test_default_registry_searches_without_plugin_runtime(native_boundary, monkeypatch):
    item = native_boundary
    item.runtime.plugins.side_effect = AssertionError("Core metadata must not use a plugin runtime")
    item.session.add(
        PluginMetadataProviderRegistration(
            provider_id="removed.plugin.provider",
            plugin_id="removed.plugin",
            installation_id=uuid4(),
            declaration=item.declaration,
            registered_at=0,
        )
    )
    item.session.commit()
    discovered = await providers.discover_providers(item.database, item.users[0].id)
    assert {provider.id for provider in discovered} == set(core.CORE_PROVIDERS)
    assert len(discovered) == 7
    calls = []

    async def network(url, **_options):
        calls.append(url)
        return {
            "items": [{"id": 620, "name": "Portal 2", "tiny_image": "https://ignored.test/art"}]
        }

    monkeypatch.setattr(core_http, "_network", network)
    provider = next(provider for provider in discovered if provider.name == "Steam")
    response = await provider.invoke(
        "search",
        MetadataProviderRequest(
            request_id=uuid4(),
            user_id=item.users[0].id,
            query="Portal 2",
            media_type=MediaType.GAME,
        ),
    )
    assert response.candidates[0].provider == "core.steam"
    assert response.candidates[0].provider_ids == {"steam": "620"}
    assert response.metadata is None and response.assets == ()
    assert len(calls) == 1 and "storesearch" in calls[0]
    item.runtime.plugins.assert_not_awaited()


@pytest.mark.asyncio
async def test_tvmaze_native_search_details_episodes_and_selected_artwork(monkeypatch):
    show = {
        "id": 526,
        "name": "The Office",
        "premiered": "2005-03-24",
        "status": "Ended",
        "externals": {"imdb": "tt0386676", "thetvdb": 73244},
        "summary": "<p>A workplace comedy.</p>",
        "averageRuntime": 22,
        "genres": ["Comedy"],
        "rating": {"average": 8.5},
        "image": {"original": "https://images.example.test/poster.jpg"},
    }
    episode = {
        "number": 1,
        "season": 1,
        "name": "Pilot",
        "airdate": "2005-03-24",
        "airstamp": "2005-03-25T02:00:00+00:00",
        "runtime": 22,
        "image": {"original": "https://images.example.test/episode.jpg"},
    }

    async def network(url, **_options):
        if "/search/" in url:
            return [{"show": show}]
        if "/episodes" in url:
            return [episode]
        if "/seasons" in url:
            return [{"number": 1, "episodeOrder": 6}]
        return show

    monkeypatch.setattr(core_http, "_network", network)
    token = core_http.context.set(({}, {}))
    try:
        values = {"request": {"query": "The Office", "media_type": "tv_show", "limit": 1}}
        result = await tvmaze.search(values)
        assert result["candidates"][0]["provider_ids"]["tvdb"] == "73244"
        assert "assets" not in result
        values["candidate"] = result["candidates"][0]
        entity = (await tvmaze.metadata(values))["metadata"]
        assert entity["description"] == "A workplace comedy."
        assert entity["seasons"][0]["episode_count"] == 6
        values["request"]["resource"] = "episodes"
        values["request"]["season_number"] = 1
        episodes = (await tvmaze.metadata(values))["metadata"]["episodes"]
        assert episodes[0]["title"] == "Pilot"
        assert episodes[0]["still_url"] == episode["image"]["original"]
        assert (await tvmaze.media(values))["assets"][0]["kind"] == "poster"
        assert (await tvmaze.health(values))["health"] == "healthy"
    finally:
        core_http.context.reset(token)


@pytest.mark.asyncio
async def test_omdb_native_identity_search_and_exact_enrichment(monkeypatch):
    requests = []

    async def network(url, **_options):
        requests.append(url)
        if "s=" in url:
            return {
                "Search": [{"imdbID": "tt0133093", "Title": "The Matrix", "Year": "1999"}],
                "totalResults": "1",
            }
        return {
            "imdbID": "tt0133093",
            "Title": "The Matrix",
            "Released": "31 Mar 1999",
            "Runtime": "136 min",
            "Plot": "A hacker discovers a simulated world.",
            "Poster": "https://images.example.test/matrix.jpg",
        }

    monkeypatch.setattr(core_http, "_network", network)
    token = core_http.context.set(({}, {"api_key": "test-only-key"}))
    try:
        values = {"request": {"query": "The Matrix", "media_type": "movie", "limit": 1}}
        result = await omdb.search(values)
        assert len(requests) == 1 and "assets" not in result
        assert result["candidates"][0]["year"] == 1999
        values["candidate"] = result["candidates"][0]
        entity = (await omdb.metadata(values))["metadata"]
        assert entity["runtime_minutes"] == 136
        assert entity["release_date"] == "1999-03-31"
        assert "i=tt0133093" in requests[-1]
        assert (await omdb.media(values))["assets"][0]["kind"] == "poster"
        assert (await omdb.health(values))["health"] == "healthy"
    finally:
        core_http.context.reset(token)


@pytest.mark.asyncio
async def test_old_credentials_migrate_encrypted_without_overwriting_new_or_cleared_scopes(
    native_boundary,
):
    item = native_boundary
    settings = item.session.scalar(select(AppIntegrationSettings))
    settings.steamgriddb_api_key = encrypt_secret("old-system-key")
    item.users[0].steamgriddb_api_key = "old-personal-key"
    item.session.commit()
    discovered = await core.discover_core_providers(item.database, item.users[0].id)
    provider = next(provider for provider in discovered if provider.name == "SteamGridDB")
    assert provider.values == {"api_key": "old-personal-key"}
    stored = list(item.session.scalars(select(PluginProviderConfiguration)))
    assert all("old-" not in row.encrypted_values for row in stored)
    row = await core.core_registration(item.database, provider.id)
    await save_configuration(item.database, row, str(item.users[0].id), {"api_key": None})
    await save_configuration(item.database, row, "system", {"api_key": "replacement"})
    again = await core.discover_core_providers(item.database, item.users[0].id)
    updated = next(provider for provider in again if provider.name == "SteamGridDB")
    assert updated.values == {"api_key": "replacement"}
    assert updated.revision != provider.revision
    await save_configuration(item.database, row, "system", {"api_key": None})
    cleared = await core.discover_core_providers(item.database, item.users[0].id)
    assert next(provider for provider in cleared if provider.name == "SteamGridDB").values == {}


@pytest.mark.asyncio
async def test_replaced_credential_context_cannot_be_reused(native_boundary, monkeypatch):
    item = native_boundary
    provider = next(
        provider
        for provider in await core.discover_core_providers(item.database, item.users[0].id)
        if provider.name == "OMDb"
    )
    row = await core.core_registration(item.database, provider.id)
    await save_configuration(item.database, row, "system", {"api_key": "changed"})
    operation = AsyncMock()
    monkeypatch.setattr(core.CORE_PROVIDERS[provider.id], "search", operation)
    response = await provider.invoke(
        "search",
        MetadataProviderRequest(
            request_id=uuid4(), user_id=item.users[0].id, query="Dune", media_type=MediaType.MOVIE
        ),
    )
    assert response.failure.code == "invalid_configuration"
    operation.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_http_is_cancelled_and_interactive_does_not_retry(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    monkeypatch.setattr(core_http, "_network", slow)
    task = asyncio.create_task(core_http.ProviderHttp({}, "cancel-test")("https://example.test"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    failure = AsyncMock(side_effect=core_http.ProviderFailure("rate_limited", 120))
    monkeypatch.setattr(core_http, "_network", failure)
    with pytest.raises(core_http.ProviderFailure, match="rate_limited"):
        await core_http.ProviderHttp({}, "retry-test")("https://example.test")
    assert failure.await_count == 1


@pytest.mark.asyncio
async def test_anizip_preserves_episode_details_with_documented_id(monkeypatch):
    http = AsyncMock(
        return_value={
            "episodeCount": 1,
            "mappings": {"anilist_id": 20},
            "episodes": {
                "1": {
                    "title": {"en": "Arrival"},
                    "overview": "Summary\nSource: credit",
                    "airDate": "2002-10-03",
                    "runtime": 24,
                    "image": "https://example.test/still.jpg",
                },
                "S1": {"title": {"en": "Special"}},
                "2": {"title": {"en": "Extra"}},
            },
        }
    )
    monkeypatch.setattr(anizip, "ProviderHttp", lambda *args, **kwargs: http)
    response = await anizip.metadata(
        {"request": {"resource": "episodes"}, "candidate": {"provider_ids": {"anilist": "20"}}}
    )
    assert http.await_args.args[0] == "https://api.ani.zip/mappings?anilist_id=20"
    assert response["metadata"]["episodes"] == [
        {
            "episode_number": 1,
            "title": "Arrival",
            "description": "Summary",
            "air_date": "2002-10-03",
            "air_at": None,
            "runtime_minutes": 24,
            "still_url": "https://example.test/still.jpg",
        }
    ]


@pytest.mark.asyncio
async def test_anilist_titles_and_stills_leave_space_for_primary_episode_source(monkeypatch):
    monkeypatch.setattr(
        anilist,
        "entity",
        AsyncMock(
            return_value={
                "id": 20,
                "episodes": 3,
                "status": "FINISHED",
                "streamingEpisodes": [
                    {"title": "Episode 1 - Arrival", "thumbnail": "https://example.test/one.jpg"},
                    {"title": "Untitled", "thumbnail": "https://example.test/two.jpg"},
                ],
            }
        ),
    )
    response = await anilist.metadata({"request": {"resource": "episodes"}, "candidate": {}})
    entries = response["metadata"]["episodes"]
    assert entries[0]["title"] == "Arrival"
    assert entries[1]["title"] is None and entries[1]["still_url"]
    assert entries[2] == {"episode_number": 3}


@pytest.mark.asyncio
async def test_core_configuration_http_keeps_scopes_private_without_plugin_grants(native_boundary):
    item = native_boundary
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=item.app),
        base_url="http://test",
        cookies={session_cookie_name("test"): item.tokens[item.users[0].id]},
    ) as client:
        url = "/api/metadata/providers/core.steamgriddb/configuration"
        response = await client.put(
            url, json={"scope": "system", "values": {"api_key": "core-secret"}}
        )
        assert response.status_code == 200
        status = await client.get("/api/metadata/providers")
        assert "core-secret" not in response.text + status.text
        assert all(provider["included"] for provider in status.json()["providers"])
        item.users[0].is_admin = False
        item.session.commit()
        assert (
            await client.put(url, json={"scope": "system", "values": {"api_key": "forbidden"}})
        ).status_code == 403
        assert (
            await client.put(url, json={"scope": "user", "values": {"api_key": "personal"}})
        ).status_code == 200
        assert (
            await client.put(url, json={"scope": "user", "values": {"unknown": "ignored"}})
        ).status_code == 422


@pytest.mark.asyncio
async def test_native_franchise_reparents_duology_using_shared_workflow():
    first = {"id": 2, "title": {"english": "First movie"}, "format": "MOVIE"}
    second = {"id": 3, "title": {"english": "Second movie"}, "format": "MOVIE"}
    nodes = {
        1: {
            "id": 1,
            "format": "TV",
            "title": {"english": "Series"},
            "relations": {
                "edges": [
                    {"relationType": "ALTERNATIVE", "node": second},
                    {"relationType": "ALTERNATIVE", "node": first},
                ]
            },
        },
        2: first,
        3: {**second, "relations": {"edges": [{"relationType": "PREQUEL", "node": first}]}},
    }
    graph = FranchiseGraph({})

    async def fetch(_query, variables):
        return {"data": {"Media": nodes[variables["id"]]}}

    graph._post_graphql = fetch
    result = await graph.relations_chain_and_branches("Series", "1")
    assert [entry["id"] for entry in result["branches"]] == [2, 3]
    assert result["branches"][1]["anchor_id"] == 2
    assert result["branches"][1]["anchor_kind"] == "branch"
