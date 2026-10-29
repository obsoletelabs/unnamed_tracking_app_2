"""Request amplification regressions at the existing runtime boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.api.routes.plugin_manager import runtime
from src.plugin_api.manager_state import ManagerState


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
