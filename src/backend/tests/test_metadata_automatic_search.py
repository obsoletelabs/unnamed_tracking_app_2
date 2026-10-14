"""Policy, credential ownership and explicit-search regressions at the public boundary."""

from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_core_metadata import native_boundary  # noqa: F401
from test_metadata_provider_configuration import provider_boundary  # noqa: F401
from test_plugin_authorization_http import boundary  # noqa: F401
from test_progressive_metadata import ContractProvider, candidate, eventually, request

from src.api.routes import metadata as routes
from src.core.auth import session_cookie_name
from src.core.preferences import save_preferences, validate_preference
from src.features.metadata.automatic_search import (
    automatic_search_status,
    effective_automatic_search,
    save_server_policy,
)
from src.features.metadata.configuration import save_configuration
from src.features.metadata.core import discover_core_providers
from src.features.metadata.handler import MetadataHandler


@pytest.mark.parametrize("value", [None, [], {"core.steam": 1}, {"bad/key": False}])
def test_invalid_provider_choices_are_rejected(value):
    with pytest.raises(ValueError):
        validate_preference("metadata_automatic_search_providers", value)


@pytest.mark.parametrize(
    "account,provider,server,personal,expected",
    [
        (False, True, True, True, False),
        (True, False, True, True, False),
        (True, True, False, False, False),
        (True, True, False, True, True),
        (True, True, True, False, True),
    ],
)
def test_policy_precedence(account, provider, server, personal, expected):
    enabled, _reason = effective_automatic_search(
        account_enabled=account,
        provider_enabled=provider,
        server_enabled=server,
        uses_personal_credentials=personal,
    )
    assert enabled is expected


@pytest.mark.asyncio
async def test_personal_credentials_override_server_limits_for_only_their_owner(native_boundary):  # noqa: F811 - imported fixture
    item = native_boundary
    providers = await discover_core_providers(item.database, item.users[0].id)
    art = next(provider for provider in providers if provider.id == "core.steamgriddb")
    row = await routes.core_registration(item.database, art.id)
    await save_configuration(item.database, row, "system", {"api_key": "shared-test-key"})
    await save_server_policy(item.database, None, False)
    assert not (await automatic_search_status(item.database, art))["enabled"]
    await save_configuration(
        item.database, row, str(item.users[0].id), {"api_key": "personal-test-key"}
    )
    assert (await automatic_search_status(item.database, art))["enabled"]
    other = replace(art, user_id=item.users[1].id)
    assert not (await automatic_search_status(item.database, other))["enabled"]
    await save_preferences(item.database, art.user_id, {"metadata_automatic_search_enabled": False})
    assert not (await automatic_search_status(item.database, art))["enabled"]
    await save_preferences(
        item.database,
        art.user_id,
        {
            "metadata_automatic_search_enabled": True,
            "metadata_automatic_search_providers": {art.id: False},
        },
    )
    assert not (await automatic_search_status(item.database, art))["enabled"]


@pytest.mark.asyncio
async def test_server_limits_apply_to_credential_free_providers(native_boundary):  # noqa: F811 - imported fixture
    item = native_boundary
    provider = next(
        p
        for p in await discover_core_providers(item.database, item.users[0].id)
        if p.id == "core.steam"
    )
    assert (await automatic_search_status(item.database, provider))["enabled"]
    await save_server_policy(item.database, provider.id, False)
    assert not (await automatic_search_status(item.database, provider))["enabled"]


@pytest.mark.asyncio
async def test_server_policy_requires_an_administrator(native_boundary):  # noqa: F811 - imported fixture
    item = native_boundary
    item.users[0].is_admin = False
    item.session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=item.app), base_url="http://test"
    ) as client:
        response = await client.put(
            "/api/metadata/automatic-search",
            json={"enabled": False},
            cookies={session_cookie_name("test"): item.tokens[item.users[0].id]},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_automatic_request_does_not_validate_blocked_providers(native_boundary, monkeypatch):  # noqa: F811 - imported fixture
    item = native_boundary
    await save_server_policy(item.database, None, False)
    monkeypatch.setattr(routes, "get_or_create_scan_settings", AsyncMock(return_value=None))
    monkeypatch.setattr(routes, "_scan_settings_to_preferences", lambda _: {})
    validation = []
    monkeypatch.setattr(routes.monitor, "schedule", lambda provider: validation.append(provider.id))
    snapshot = await routes.start_search(
        routes.SearchInput(query="Portal", trigger="automatic"), item.database, item.users[0]
    )
    session = routes.handler.owned(snapshot["id"], item.users[0].id)
    await session.search_task
    assert session.candidates == {}
    assert validation == []
    routes.handler.cancel(session)


@pytest.mark.asyncio
async def test_blocked_automatic_provider_can_enrich_after_explicit_selection():
    handler = MetadataHandler()
    search_provider = ContractProvider("test.search", (candidate(),))
    blocked = ContractProvider("test.art")
    blocked.automatic_search_enabled = False
    work = request("Portal").model_copy(update={"options": {"automatic_search": True}})
    session = handler.start(work, [search_provider, blocked])
    await session.search_task
    assert blocked.calls == []
    handler.select(session, next(iter(session.candidates)))
    await eventually(lambda: ("media", "1") in blocked.calls)
    assert ("search", None) not in blocked.calls
    handler.cancel(session)


@pytest.mark.asyncio
async def test_explicit_search_ignores_typing_policy():
    provider = ContractProvider("test.search", (candidate(),))
    provider.automatic_search_enabled = False
    handler = MetadataHandler()
    session = handler.start(request("por"), [provider])
    await session.search_task
    assert ("search", None) in provider.calls
    assert len(session.candidates) == 1
    handler.cancel(session)
