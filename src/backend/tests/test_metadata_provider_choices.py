"""Account metadata choices cover builtin/plugin invocations and cached sessions."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from test_core_metadata import native_boundary  # noqa: F401
from test_metadata_provider_configuration import provider_boundary, register  # noqa: F401
from test_plugin_authorization_http import boundary, grant  # noqa: F401

from src.api.routes import metadata as routes
from src.api.routes.settings import get_or_create_scan_settings
from src.core.auth import session_cookie_name
from src.core.preferences import load_preferences, save_preferences, validate_preference
from src.database.models.user_preferences import UserPreferences
from src.database.models.user_scan_settings import DEFAULT_PROVIDER_ORDER, UserScanSettings
from src.features.metadata import core, providers
from src.features.metadata.configuration import save_configuration
from src.features.metadata.handler import MetadataHandler
from src.plugin_api.metadata_contracts import (
    MediaType,
    MetadataCandidate,
    MetadataProviderRequest,
    ProviderHealth,
)


def set_choices(item, user, choices):
    row = item.session.scalar(select(UserPreferences).where(UserPreferences.user_id == user.id))
    if row is None:
        row = UserPreferences(user_id=user.id, data={})
        item.session.add(row)
    row.data = {**row.data, "metadata_providers_enabled": choices}
    item.session.commit()


def work(user, media_type=MediaType.TV_SHOW):
    return MetadataProviderRequest(
        request_id=uuid4(), user_id=user.id, query="A title", media_type=media_type
    )


@pytest.fixture
def policy_boundary(native_boundary, monkeypatch):  # noqa: F811
    item = native_boundary

    @asynccontextmanager
    async def session():
        yield item.database

    monkeypatch.setattr(providers, "SessionLocal", session)
    return item


def test_account_provider_choices_are_bounded_boolean_overrides():
    choices = {"core.steam": False, "official.metadata-gog.provider": True}
    assert validate_preference("metadata_providers_enabled", choices) == choices
    for invalid in (None, [], {"core.steam": 1}, {"wrong/key": False}, {"x" * 129: True}):
        with pytest.raises(ValueError):
            validate_preference("metadata_providers_enabled", invalid)
    with pytest.raises(ValueError):
        validate_preference("metadata_providers_enabled", {f"p{i}": False for i in range(257)})


def test_only_unsaved_provider_order_prefers_steam_to_gog():
    assert DEFAULT_PROVIDER_ORDER.index("Steam") < DEFAULT_PROVIDER_ORDER.index("GOG")


@pytest.mark.parametrize("operation", ["search", "metadata", "media", "health"])
async def test_disabled_core_provider_never_runs_even_from_a_stale_snapshot(
    policy_boundary, monkeypatch, operation
):
    item = policy_boundary
    owner = item.users[0]
    snapshot = next(
        p
        for p in await providers.discover_providers(item.database, owner.id)
        if p.id == "core.tvmaze"
    )
    call = AsyncMock(return_value={"health": "healthy"})
    monkeypatch.setattr(core.CORE_PROVIDERS[snapshot.id], operation, call)
    set_choices(item, owner, {snapshot.id: False})
    candidate = MetadataCandidate(
        title="A title",
        media_type=MediaType.TV_SHOW,
        provider="core.tvmaze",
        external_id="42",
        provider_ids={"tvmaze": "42"},
    )
    await snapshot.invoke(operation, work(owner), candidate)
    call.assert_not_awaited()
    assert not await snapshot.authorized(operation)
    discovered = next(
        p
        for p in await providers.discover_providers(item.database, owner.id)
        if p.id == snapshot.id
    )
    assert discovered.state == ProviderHealth.DISABLED


async def test_disabled_plugin_provider_cannot_use_shared_credentials_or_cached_results(
    policy_boundary,
):
    item = policy_boundary
    owner = item.users[0]
    row = await register(item)
    await save_configuration(item.database, row, "system", {"api_key": "test-server-key"})
    snapshot = next(
        p
        for p in await providers.discover_providers(item.database, owner.id)
        if p.id == row.provider_id
    )
    candidate = {
        "title": "A title",
        "media_type": "game",
        "provider": snapshot.id,
        "external_id": "42",
        "provider_ids": {"steam": "42"},
    }
    item.runtime.action.return_value = {"candidates": [candidate]}
    handler = MetadataHandler()
    request = work(owner, MediaType.GAME)
    session = handler.start(request, [snapshot])
    await session.search_task
    assert session.candidates
    handler.cancel(session)
    item.runtime.action.reset_mock()
    set_choices(item, owner, {snapshot.id: False})
    session = handler.start(request, [snapshot])
    await session.search_task
    assert not session.candidates
    handler.cancel(session)
    item.runtime.action.assert_not_awaited()
    assert not await snapshot.authorized("search")


async def test_catalog_keeps_disabled_providers_available_to_reenable_for_only_the_owner(
    policy_boundary,
):
    item = policy_boundary
    set_choices(item, item.users[0], {"core.tvmaze": False})
    for user, expected in ((item.users[0], False), (item.users[1], True)):
        status = await routes.providers_status(item.database, user)
        provider = next(p for p in status["providers"] if p["provider_id"] == "core.tvmaze")
        assert provider["account_enabled"] is expected


@pytest.mark.parametrize("operation", ["search", "metadata", "media", "health"])
async def test_direct_plugin_metadata_actions_cannot_bypass_an_account_optout(
    policy_boundary, operation
):
    item = policy_boundary
    item.declaration["operations"]["media"] = "media"
    row = await register(item)
    set_choices(item, item.users[0], {row.provider_id: False})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=item.app), base_url="http://test"
    ) as client:
        client.cookies.set(session_cookie_name("test"), item.tokens[item.users[0].id])
        response = await client.post(
            f"/api/plugins/{item.plugin['plugin_id']}/actions/{operation}",
            json={"values": {"provider_id": row.provider_id}},
        )
    assert response.status_code == 403, response.text
    assert "disabled for your account" in response.text
    item.runtime.action.assert_not_awaited()


async def test_reenabling_retains_credentials_and_does_not_change_typing_choices(policy_boundary):
    item = policy_boundary
    owner = item.users[0]
    row = await register(item)
    await save_preferences(
        item.database,
        owner.id,
        {
            "metadata_automatic_search_providers": {row.provider_id: False},
        },
    )
    await save_configuration(item.database, row, "system", {"api_key": "kept-test-key"})
    set_choices(item, owner, {row.provider_id: False})
    with pytest.raises(PermissionError, match="disabled for your account"):
        await providers.provider_configuration(
            item.database,
            item.plugin["plugin_id"],
            item.installation_id,
            owner.id,
            {"provider_id": row.provider_id},
        )
    set_choices(item, owner, {row.provider_id: True})
    configured = await providers.provider_configuration(
        item.database,
        item.plugin["plugin_id"],
        item.installation_id,
        owner.id,
        {"provider_id": row.provider_id},
    )
    assert configured["values"]["api_key"] == "kept-test-key"
    prefs = await load_preferences(item.database, owner.id)
    assert prefs["metadata_automatic_search_providers"][row.provider_id] is False


async def test_metadata_optout_keeps_the_same_plugins_game_sync_action_available(policy_boundary):
    item = policy_boundary
    row = await register(item)
    set_choices(item, item.users[0], {row.provider_id: False})
    grant(item, "games.write")
    item.runtime.plugin_ui.return_value["actions"].append(
        {"id": "sync-library", "capability": {"name": "games.write", "version": 1}}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=item.app), base_url="http://test"
    ) as client:
        client.cookies.set(session_cookie_name("test"), item.tokens[item.users[0].id])
        response = await client.post(
            f"/api/plugins/{item.plugin['plugin_id']}/actions/sync-library", json={"values": {}}
        )
    assert response.status_code == 200, response.text
    item.runtime.action.assert_awaited_once()


async def test_background_metadata_respects_account_optouts(policy_boundary, monkeypatch):
    from src.features.metadata import core_http, service

    item = policy_boundary
    set_choices(item, item.users[0], {provider_id: False for provider_id in core.CORE_PROVIDERS})
    network = AsyncMock(side_effect=AssertionError("Disabled metadata must not fetch"))
    monkeypatch.setattr(core_http, "_network", network)
    candidate = service.library_candidate("A title", MediaType.TV_SHOW, {"tvmaze": "42"})
    await service.collect_record(item.database, item.users[0].id, candidate, include_media=True)
    network.assert_not_awaited()


async def test_saved_provider_order_survives_the_new_default(policy_boundary):
    item = policy_boundary
    UserScanSettings.__table__.create(item.session.bind, checkfirst=True)
    saved = ["GOG", "Steam", "IGDB"]
    item.session.add(UserScanSettings(user_id=item.users[0].id, provider_order=saved))
    item.session.commit()
    existing = await get_or_create_scan_settings(item.users[0].id, item.database)
    assert existing.provider_order == saved
    new = await get_or_create_scan_settings(item.users[1].id, item.database)
    assert new.provider_order.index("Steam") < new.provider_order.index("GOG")


async def test_results_arriving_after_an_optout_are_not_applied(policy_boundary, monkeypatch):
    item = policy_boundary
    owner = item.users[0]
    snapshot = next(
        p
        for p in await providers.discover_providers(item.database, owner.id)
        if p.id == "core.tvmaze"
    )

    async def incoming(_values):
        set_choices(item, owner, {snapshot.id: False})
        return {"metadata": {"description": "Arrived after disabling"}}

    monkeypatch.setattr(core.CORE_PROVIDERS[snapshot.id], "metadata", incoming)
    response = await snapshot.invoke("metadata", work(owner))
    assert response.metadata is None


async def test_ambiguous_legacy_action_cannot_choose_an_opted_out_provider(policy_boundary):
    item = policy_boundary
    first = await register(item)
    other_id = "audit.plugin.another"
    await providers.register_provider(
        item.database,
        item.plugin["plugin_id"],
        item.installation_id,
        {**item.declaration, "provider_id": other_id},
    )
    set_choices(item, item.users[0], {first.provider_id: False})
    arguments = {
        "plugin_id": item.plugin["plugin_id"],
        "installation_id": item.installation_id,
        "user_id": item.users[0].id,
        "action_id": "search",
        "operation": "search",
    }
    with pytest.raises(PermissionError, match="disabled for your account"):
        await providers.authorize_metadata_action(item.database, **arguments)
    await providers.authorize_metadata_action(item.database, **arguments, provider_id=other_id)


@pytest.mark.parametrize("kind", ["core", "plugin"])
async def test_provider_snapshot_cannot_be_reused_for_another_actor(
    policy_boundary, monkeypatch, kind
):
    item = policy_boundary
    provider_id = "core.steam"
    if kind == "plugin":
        row = await register(item)
        await save_configuration(item.database, row, "system", {"api_key": "actor-test-key"})
        provider_id = row.provider_id
    snapshot = next(
        p
        for p in await providers.discover_providers(item.database, item.users[0].id)
        if p.id == provider_id
    )
    native_call = AsyncMock(return_value={"candidates": []})
    monkeypatch.setattr(core.CORE_PROVIDERS["core.steam"], "search", native_call)
    response = await snapshot.invoke("search", work(item.users[1], MediaType.GAME))
    assert response.failure is not None
    native_call.assert_not_awaited()
    item.runtime.action.assert_not_awaited()
