"""Native IGDB requests reuse authentication without crossing credential contexts."""

import asyncio
import time
from urllib.parse import parse_qs, urlparse

import pytest

from src.features.metadata import core_http
from src.features.metadata.builtin import igdb


@pytest.fixture(autouse=True)
def isolated_provider_state():
    core_http._locks.clear()
    core_http._next_at.clear()
    igdb._tokens.clear()
    handle = core_http.context.set(({}, {"client_id": "client", "client_secret": "secret"}))
    yield
    core_http.context.reset(handle)
    igdb._tokens.clear()


def token_response(token="token", expires_in=3600):
    return {"access_token": token, "expires_in": expires_in}


async def request():
    return await igdb.games({"request": {}}, "fields name; limit 1;")


async def test_search_metadata_and_artwork_share_a_token(monkeypatch):
    calls = []

    async def network(url, **options):
        calls.append(url)
        if "oauth2/token" in url:
            return token_response()
        return [{"id": 1, "name": "Portal", "cover": {"url": "//images.example/cover"}}]

    monkeypatch.setattr(core_http, "_network", network)
    values = {"request": {"query": "Portal"}, "candidate": {"provider_ids": {"igdb": "1"}}}
    assert (await igdb.search(values))["candidates"][0]["title"] == "Portal"
    assert (await igdb.metadata(values))["metadata"]["title"] == "Portal"
    assert (await igdb.media(values))["assets"]
    assert sum("oauth2/token" in url for url in calls) == 1


async def test_concurrent_requests_share_token_refresh(monkeypatch):
    tokens = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def network(url, **_options):
        nonlocal tokens
        if "oauth2/token" in url:
            tokens += 1
            entered.set()
            await release.wait()
            return token_response()
        return []

    monkeypatch.setattr(core_http, "_network", network)
    first = asyncio.create_task(request())
    await entered.wait()
    second = asyncio.create_task(request())
    release.set()
    assert await asyncio.gather(first, second) == [[], []]
    assert tokens == 1


async def test_rotated_credentials_request_their_own_token(monkeypatch):
    tokens = []
    authorizations = []

    async def network(url, **options):
        if "oauth2/token" in url:
            secret = parse_qs(urlparse(url).query)["client_secret"][0]
            tokens.append(secret)
            return token_response(secret)
        authorizations.append(options["headers"]["Authorization"])
        return []

    monkeypatch.setattr(core_http, "_network", network)
    await request()
    handle = core_http.context.set(({}, {"client_id": "client", "client_secret": "rotated"}))
    try:
        await request()
    finally:
        core_http.context.reset(handle)
    await request()
    assert tokens == ["secret", "rotated"]
    assert authorizations == ["Bearer secret", "Bearer rotated", "Bearer secret"]


async def test_expired_token_is_refreshed(monkeypatch):
    tokens = 0

    async def network(url, **_options):
        nonlocal tokens
        if "oauth2/token" in url:
            tokens += 1
            return token_response(str(tokens))
        return []

    monkeypatch.setattr(core_http, "_network", network)
    await request()
    next(iter(igdb._tokens.values())).expires_at = 0
    await request()
    assert tokens == 2


@pytest.mark.parametrize("lifetime", [None, True, 0, -1, "3600", float("inf")])
async def test_invalid_lifetime_is_not_cached(monkeypatch, lifetime):
    async def network(_url, **_options):
        return token_response(expires_in=lifetime)

    monkeypatch.setattr(core_http, "_network", network)
    for _ in range(2):
        with pytest.raises(core_http.ProviderFailure, match="invalid_response"):
            await request()
    assert all(state.value is None for state in igdb._tokens.values())


async def test_waiting_for_refresh_uses_own_deadline(monkeypatch):
    entered = asyncio.Event()
    original_http = core_http.ProviderHttp

    def transport(work, *args, **kwargs):
        http = original_http(work, *args, **kwargs)
        if not http.background:
            http.deadline = time.monotonic() + 0.05
        return http

    async def network(_url, **_options):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(igdb, "ProviderHttp", transport)
    monkeypatch.setattr(core_http, "_network", network)
    first = asyncio.create_task(igdb.games({"request": {"policy": "background"}}, "fields name;"))
    await entered.wait()
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(request(), 1)
        assert not first.done()
    finally:
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first


async def test_rejected_token_is_refreshed_once(monkeypatch):
    tokens = 0
    authorizations = []

    async def network(url, **options):
        nonlocal tokens
        if "oauth2/token" in url:
            tokens += 1
            return token_response(str(tokens))
        authorization = options["headers"]["Authorization"]
        authorizations.append(authorization)
        if authorization == "Bearer 1":
            raise core_http.ProviderFailure("invalid_configuration", status=401)
        return []

    monkeypatch.setattr(core_http, "_network", network)
    await request()
    await request()
    assert tokens == 2
    assert authorizations == ["Bearer 1", "Bearer 2", "Bearer 2"]


async def test_repeated_rejection_does_not_loop(monkeypatch):
    tokens = 0
    queries = 0

    async def network(url, **_options):
        nonlocal tokens, queries
        if "oauth2/token" in url:
            tokens += 1
            return token_response(str(tokens))
        queries += 1
        raise core_http.ProviderFailure("invalid_configuration", status=401)

    monkeypatch.setattr(core_http, "_network", network)
    with pytest.raises(core_http.ProviderFailure, match="invalid_configuration"):
        await request()
    assert tokens == queries == 2


@pytest.mark.parametrize("status", [400, 403, 429, 503])
async def test_other_failures_do_not_refresh_token(monkeypatch, status):
    tokens = 0

    async def network(url, **_options):
        nonlocal tokens
        if "oauth2/token" in url:
            tokens += 1
            return token_response()
        raise core_http.ProviderFailure("unavailable", status=status)

    monkeypatch.setattr(core_http, "_network", network)
    with pytest.raises(core_http.ProviderFailure):
        await request()
    assert tokens == 1


async def test_cancelled_refresh_releases_waiting_requests(monkeypatch):
    tokens = 0
    entered = asyncio.Event()

    async def network(url, **_options):
        nonlocal tokens
        if "oauth2/token" in url:
            tokens += 1
            if tokens == 1:
                entered.set()
                await asyncio.Event().wait()
            return token_response()
        return []

    monkeypatch.setattr(core_http, "_network", network)
    first = asyncio.create_task(request())
    await entered.wait()
    second = asyncio.create_task(request())
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await asyncio.wait_for(second, 2) == []
    assert tokens == 2
