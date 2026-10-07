"""Persisted public-contract security and scoped credential regressions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import JSON, select
from test_plugin_authorization_http import PersistedDb, boundary, grant  # noqa: F401

from src.api.routes import metadata as routes
from src.core.auth import session_cookie_name
from src.database.models.plugin_metadata_provider import (
    PluginMetadataProviderRegistration,
    PluginProviderConfiguration,
)
from src.database.session import get_db
from src.features.metadata import providers
from src.features.metadata.health import ProviderHealthMonitor
from src.plugin_api.metadata_contracts import ProviderFailure, ProviderHealth, ProviderResponse


@pytest.fixture
def provider_boundary(boundary, monkeypatch):  # noqa: F811 - imported pytest fixture
    monkeypatch.setattr(PluginMetadataProviderRegistration.__table__.c.declaration, "type", JSON())
    for model in (PluginMetadataProviderRegistration, PluginProviderConfiguration):
        model.__table__.create(boundary.session.bind)

    class Database(PersistedDb):
        async def get(self, model, key):
            return self.session.get(model, key)

    db = Database(boundary.session)

    async def dependency():
        yield db

    boundary.app.dependency_overrides[get_db] = dependency
    original_routes = list(boundary.app.router.routes)
    boundary.app.router.routes.clear()
    boundary.app.include_router(routes.router)
    boundary.app.router.routes.extend(original_routes)
    boundary.plugin["api_contract_version"] = "1.1.1"
    boundary.runtime.plugin_ui.return_value["actions"] = [
        {"id": operation, "capability": {"name": "metadata_providers." + operation}}
        for operation in ("search", "metadata", "media", "health")
    ]
    monkeypatch.setattr(providers, "PluginRuntimeClient", lambda: boundary.runtime)
    monkeypatch.setattr(routes.monitor, "schedule", lambda *args, **kwargs: None)
    for capability in ("register", "configuration", "search", "metadata", "media", "health"):
        grant(boundary, "metadata_providers." + capability)
    declaration = {
        "provider_id": "audit.plugin.provider", "name": "Provider", "media_types": ["game"],
        "operations": {"search": "search", "metadata": "metadata", "health": "health"},
        "configuration": [
            {"key": "api_key", "label": "API key", "scope": "both"},
            {"key": "developer_key", "label": "Developer key", "scope": "system", "required": False},
            {"key": "account", "label": "Account", "scope": "user", "required": False},
        ],
    }
    return SimpleNamespace(**vars(boundary), database=db, declaration=declaration)


async def register(item):
    await providers.register_provider(item.database, item.plugin["plugin_id"],
                                      item.installation_id, item.declaration)
    return await item.database.get(PluginMetadataProviderRegistration, item.declaration["provider_id"])


@pytest.mark.asyncio
async def test_registration_validates_namespace_actions_and_installation(provider_boundary):
    item = provider_boundary
    await register(item)
    await register(item)
    assert len(list(item.session.scalars(select(PluginMetadataProviderRegistration)))) == 1
    with pytest.raises(PermissionError):
        await providers.register_provider(item.database, item.plugin["plugin_id"], item.installation_id,
                                          {**item.declaration, "provider_id": "another.plugin.provider"})
    item.runtime.plugin_ui.return_value["actions"][0]["capability"]["name"] = "games.read"
    with pytest.raises(ValueError, match="matching action capability"):
        await register(item)
    with pytest.raises(PermissionError):
        await providers.unregister_provider(item.database, item.plugin["plugin_id"], uuid4(),
                                            {"provider_id": item.declaration["provider_id"]})


@pytest.mark.asyncio
async def test_configuration_is_encrypted_scoped_patched_and_installation_bound(provider_boundary):
    item = provider_boundary
    row = await register(item)
    await providers.save_configuration(item.database, row, "system", {"api_key": "system-secret"})
    await providers.save_configuration(item.database, row, str(item.users[0].id), {"api_key": "alice-secret", "account": "alice"})
    stored = list(item.session.scalars(select(PluginProviderConfiguration)))
    assert all("secret" not in config.encrypted_values and "alice" not in config.encrypted_values for config in stored)
    alice, revision = await providers.configuration_values(item.database, row, item.users[0].id)
    bob, _ = await providers.configuration_values(item.database, row, item.users[1].id)
    assert alice == {"api_key": "alice-secret", "account": "alice"}
    assert bob == {"api_key": "system-secret"}
    await providers.save_configuration(item.database, row, str(item.users[0].id), {"api_key": None})
    patched, changed = await providers.configuration_values(item.database, row, item.users[0].id)
    assert patched == {"api_key": "system-secret", "account": "alice"}
    assert changed != revision
    with pytest.raises(ValueError):
        await providers.save_configuration(item.database, row, str(item.users[0].id), {"developer_key": "forbidden"})
    with pytest.raises(PermissionError):
        await providers.provider_configuration(item.database, item.plugin["plugin_id"], uuid4(), item.users[0].id,
                                               {"provider_id": row.provider_id})


@pytest.mark.asyncio
async def test_http_configuration_requires_admin_for_system_and_never_returns_values(provider_boundary):
    item = provider_boundary
    row = await register(item)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=item.app), base_url="http://test",
                                cookies={session_cookie_name("test"): item.tokens[item.users[0].id]}) as client:
        response = await client.put(f"/api/metadata/providers/{row.provider_id}/configuration",
                                    json={"scope": "system", "values": {"api_key": "secret-for-test"}})
        assert response.status_code == 200, response.text
        status = await client.get("/api/metadata/providers")
        assert status.status_code == 200
        assert "secret-for-test" not in response.text + status.text
        assert status.json()["providers"][0]["configured_fields"]["api_key"] == {"system": True, "user": False}
        item.users[0].is_admin = False
        item.session.commit()
        denied = await client.put(f"/api/metadata/providers/{row.provider_id}/configuration",
                                  json={"scope": "system", "values": {"api_key": "overwrite"}})
        assert denied.status_code == 403
        personal = await client.put(f"/api/metadata/providers/{row.provider_id}/configuration",
                                    json={"scope": "user", "values": {"api_key": "personal-secret"}})
        assert personal.status_code == 200
        client.cookies.clear()
        assert (await client.get("/api/metadata/providers")).status_code == 401


@pytest.mark.asyncio
async def test_discovery_tracks_disabled_removed_reinstalled_and_revoked_plugins(provider_boundary):
    item = provider_boundary
    row = await register(item)
    await providers.save_configuration(item.database, row, "system", {"api_key": "configured"})
    assert (await providers.discover_providers(item.database, item.users[0].id))[0].state == ProviderHealth.UNVALIDATED
    item.plugin["enabled"] = False
    assert (await providers.discover_providers(item.database, item.users[0].id))[0].state == ProviderHealth.DISABLED
    item.plugin["enabled"] = True
    item.plugin["installation_id"] = str(uuid4())
    assert (await providers.discover_providers(item.database, item.users[0].id))[0].state == ProviderHealth.PLUGIN_UNAVAILABLE
    await providers.unregister_provider(item.database, item.plugin["plugin_id"], item.installation_id,
                                        {"provider_id": row.provider_id})
    assert await providers.discover_providers(item.database, item.users[0].id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,state", [
    (None, "healthy"), ("invalid_configuration", "invalid_configuration"),
    ("unavailable", "temporarily_unavailable"), ("rate_limited", "rate_limited"),
    ("plugin_unavailable", "plugin_unavailable"), ("not_configured", "not_configured"),
])
async def test_health_classifies_failures_and_revalidates_after_the_interval(monkeypatch, failure, state):
    from src.features.metadata import health

    clock = {"now": 1000}
    monkeypatch.setattr(health.time, "time", lambda: clock["now"])
    provider = SimpleNamespace(id="test.health", installation_id=uuid4(), revision="revision", user_id=uuid4(),
                               state=ProviderHealth.UNVALIDATED, declaration=SimpleNamespace(media_types=("game",)),
                               invoke=AsyncMock(return_value=ProviderResponse(
                                   failure=ProviderFailure(code=failure) if failure else None,
                                   health=ProviderHealth.HEALTHY if failure is None else None)))
    monitor = ProviderHealthMonitor(interval=1800)
    monitor.schedule(provider)
    await monitor.tasks[monitor.key(provider)]
    assert monitor.status(provider).state == state
    assert monitor.status(provider).checked_at == 1000
    monitor.schedule(provider)
    assert provider.invoke.await_count == 1
    clock["now"] = 2801
    monitor.schedule(provider)
    await monitor.tasks[monitor.key(provider)]
    assert provider.invoke.await_count == 2
    monitor.schedule(provider, force=True)
    await monitor.tasks[monitor.key(provider)]
    assert provider.invoke.await_count == 3
    assert "secret" not in json.dumps(vars(monitor.status(provider)))
