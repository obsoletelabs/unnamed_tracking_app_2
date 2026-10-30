"""Request amplification regressions at the existing runtime boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from test_plugin_authorization_http import boundary as authorization_boundary
from test_plugin_authorization_http import grant, request

from src.api.routes.plugin_manager import runtime
from src.core.auth import session_cookie_name
from src.plugin_api.manager_state import ManagerState

boundary = authorization_boundary


@pytest.mark.asyncio
async def test_single_plugin_lookup_never_enumerates_unrelated_installations(monkeypatch):
    installation = {"plugin_id": "example.one", "installation_id": str(uuid4())}
    transport = SimpleNamespace(
        plugin_state=AsyncMock(return_value=installation),
        plugins=AsyncMock(side_effect=AssertionError("unrelated inventory was scanned")),
    )
    monkeypatch.setattr(runtime, "client", transport)
    assert await runtime.live_plugin("example.one", require_enabled=False) == installation
    transport.plugin_state.assert_awaited_once_with("example.one")
    transport.plugins.assert_not_awaited()


def test_unchanged_inventory_does_not_rewrite_manager_state(tmp_path, monkeypatch):
    store = ManagerState(tmp_path / "manager.json")
    inventory = [{"plugin_id": "example.one", "enabled": True}]
    store.reconcile(inventory)
    save = Mock(wraps=store._save)
    monkeypatch.setattr(store, "_save", save)
    first = store.reconcile(inventory)
    assert first[0]["enabled"] is True
    save.assert_not_called()
    store.reconcile([{"plugin_id": "example.one", "enabled": False}])
    save.assert_called_once()


@pytest.mark.asyncio
async def test_transport_is_pooled_across_wrappers_and_closed_at_shutdown(monkeypatch):
    import httpx

    from src.plugin_api import runtime_client

    await runtime_client.close_runtime_transport()
    original = httpx.AsyncClient
    calls = []

    def response(request):
        calls.append(request.headers["X-Plugin-Runtime-Token"])
        return httpx.Response(200, json={"status": "ok"})

    factory = Mock(side_effect=lambda **kw: original(transport=httpx.MockTransport(response), **kw))
    monkeypatch.setattr(runtime_client.httpx, "AsyncClient", factory)
    one = runtime_client.PluginRuntimeClient("http://runtime", "x" * 32)
    two = runtime_client.PluginRuntimeClient("http://runtime", "y" * 32)
    try:
        await one.health()
        await two.health()
        assert calls == ["x" * 32, "y" * 32]
        factory.assert_called_once()
    finally:
        await runtime_client.close_runtime_transport()
    await one.health()
    assert factory.call_count == 2
    await runtime_client.close_runtime_transport()


@pytest.mark.asyncio
async def test_discovery_batches_documents_and_filters_cached_content_with_live_grants(boundary):
    boundary.plugin["digest"] = "a" * 64
    permission = grant(boundary, "frontend.native")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=boundary.app),
        base_url="http://test",
        cookies={session_cookie_name("test"): boundary.tokens[boundary.users[0].id]},
    ) as client:
        for _ in range(2):
            response = await client.get("/api/plugins", headers={"X-Plugin-Include-UI": "true"})
            assert response.status_code == 200, response.text
            assert response.json()[0]["ui_document"]["native_frontend"]
        boundary.runtime.plugin_ui.assert_awaited_once()
        permission.revoked_at = 123
        boundary.session.commit()
        response = await client.get("/api/plugins", headers={"X-Plugin-Include-UI": "true"})
        assert response.json()[0]["ui_document"]["native_frontend"] is None
        boundary.runtime.plugin_ui.assert_awaited_once()
        boundary.plugin["installation_id"] = str(uuid4())
        response = await client.get("/api/plugins", headers={"X-Plugin-Include-UI": "true"})
        assert response.status_code == 200, response.text
        assert boundary.runtime.plugin_ui.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["plugin.storage", "plugin.settings", "api.full"])
async def test_permission_revocation_pushes_installation_scoped_access_invalidation(
    boundary, capability
):
    permission = grant(boundary, capability)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=boundary.app),
        base_url="http://test",
        cookies={session_cookie_name("test"): boundary.tokens[boundary.users[0].id]},
    ) as client:
        response = await client.post(f"/api/plugin-permissions/grants/{permission.id}/revoke")
    assert response.status_code == 200, response.text
    boundary.runtime.invalidate_access.assert_awaited_once_with(
        permission.plugin_id,
        str(permission.installation_id),
        str(permission.user_id) if permission.user_id else None,
    )


@pytest.mark.asyncio
async def test_access_snapshot_contains_only_the_live_authorized_local_capability(boundary):
    grant(boundary, "plugin.storage")
    response = await request(
        boundary, method="capabilities.check", capability="plugin.storage", payload={}
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()["payload"]["runtime_access"]
    assert snapshot == {
        "installation_id": boundary.plugin["installation_id"],
        "user_id": str(boundary.users[0].id),
        "capabilities": ["plugin.storage"],
        "expires_in_seconds": 5,
    }
    # Remote operations never acquire a reusable local grant.
    grant(boundary, "games.read")
    response = await request(
        boundary, method="capabilities.check", capability="games.read", payload={}
    )
    assert response.status_code == 200, response.text
    assert "runtime_access" not in response.json()["payload"]


@pytest.mark.asyncio
async def test_pwa_inventory_filter_is_additive_for_older_runtimes(monkeypatch):
    from src.plugin_api import runtime_client

    await runtime_client.close_runtime_transport()
    original = httpx.AsyncClient
    calls = []

    def response(request):
        calls.append(request.url.params.get("contribution"))
        # An older runtime may ignore the query and return the full inventory.
        return httpx.Response(200, json=[{"plugin_id": "example.ordinary"}])

    monkeypatch.setattr(
        runtime_client.httpx,
        "AsyncClient",
        lambda **options: original(transport=httpx.MockTransport(response), **options),
    )
    client = runtime_client.PluginRuntimeClient("http://runtime", "x" * 32)
    try:
        assert await client.plugins(pwa_only=True) == [{"plugin_id": "example.ordinary"}]
        await client.plugins()
        assert calls == ["pwa", None]
    finally:
        await runtime_client.close_runtime_transport()
