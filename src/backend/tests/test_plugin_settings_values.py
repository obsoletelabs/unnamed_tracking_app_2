"""Declarative settings reload persisted values without exposing write-only secrets.

The host routes run against the actual plugin-runtime service and registry, and
authorization uses persisted grant rows with the production grant resolver.
"""

from __future__ import annotations

import importlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_plugin_authorization_http import boundary, grant  # noqa: F401

from src.api.routes.plugin_manager import runtime as plugin_runtime
from src.core.auth import session_cookie_name
from src.plugin_api.contracts import PluginUiDocument
from src.plugin_api.runtime_client import PluginRuntimeClient
from src.plugin_api.ui_permissions import filter_setting_values

PLUGIN_ID = "audit.plugin"
RUNTIME_TOKEN = "x" * 32
UI_DOCUMENT = {
    "schema_version": "v1",
    "api_contract_version": "1.1.0",
    "plugin_id": PLUGIN_ID,
    "title": "Password Reset",
    "settings": [
        {
            "id": "recovery",
            "title": "Account recovery",
            "fields": [
                {
                    "id": "token_lifetime_seconds",
                    "label": "Link lifetime (seconds)",
                    "type": "number",
                    "default": 1200,
                    "required": True,
                    "validation": {"minimum": 600, "maximum": 3600},
                },
                {
                    "id": "delivery",
                    "label": "Delivery",
                    "type": "select",
                    "default": "email",
                    "options": [
                        {"value": "email", "label": "Email"},
                        {"value": "push", "label": "Push"},
                    ],
                },
                {
                    "id": "channels",
                    "label": "Channels",
                    "type": "multiselect",
                    "options": [
                        {"value": "inbox", "label": "Inbox"},
                        {"value": "webhook", "label": "Webhook"},
                    ],
                },
                {"id": "note", "label": "Note", "type": "text", "default": "Default note"},
                {"id": "api_token", "label": "API token", "type": "password", "secret": True},
                {"id": "webhook_url", "label": "Webhook", "type": "text", "secret": True},
            ],
        }
    ],
    "pages": [{"id": "configure", "title": "Password recovery", "settings": ["recovery"]}],
}


@pytest.fixture
def stack(boundary, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "plugin-runtime"))
    runtime = importlib.import_module("runtime")
    package = tmp_path / "packages" / PLUGIN_ID
    package.mkdir(parents=True)
    (package / "ui.json").write_text(json.dumps(UI_DOCUMENT), encoding="utf-8")
    manifest = {
        "api_contract_version": "1.1.0",
        "plugin_id": PLUGIN_ID,
        "entrypoint": "plugin:main",
        "version": "1.0.0",
        "integrity": {"sha256": runtime.PluginRegistry.digest(package)},
    }
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    supervisor = runtime.PluginSupervisor(root=tmp_path / "work", storage_root=tmp_path / "storage")
    monkeypatch.setattr(supervisor, "running", lambda _: True)
    registry = runtime.PluginRegistry(package.parent, supervisor)
    registry._transition(
        PLUGIN_ID, enabled=True, status="running", installation_id=str(boundary.installation_id)
    )
    server = runtime.RuntimeServer(("127.0.0.1", 0), runtime.RuntimeHandler)
    server.registry = registry
    server.token = RUNTIME_TOKEN
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(
        plugin_runtime,
        "client",
        PluginRuntimeClient(f"http://127.0.0.1:{server.server_port}", RUNTIME_TOKEN),
    )
    try:
        yield SimpleNamespace(
            boundary=boundary,
            registry=registry,
            configuration=tmp_path / ".configuration" / f"{PLUGIN_ID}.json",
            alice=boundary.users[0],
            bob=boundary.users[1],
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def client_for(stack, user=None) -> httpx.AsyncClient:
    cookies = {}
    if user is not None:
        cookies[session_cookie_name("test")] = stack.boundary.tokens[user.id]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stack.boundary.app),
        base_url="http://test",
        cookies=cookies,
    )


SETTINGS = f"/api/plugins/{PLUGIN_ID}/settings"


@pytest.mark.asyncio
async def test_saved_values_replace_manifest_defaults_across_reload_and_edit_cycles(stack):
    grant(stack.boundary, "plugin.settings")
    async with client_for(stack, stack.alice) as client:
        # Nothing saved yet: no values are invented, so every field uses its default.
        initial = await client.get(SETTINGS)
        assert initial.status_code == 200, initial.text
        assert initial.json() == {"plugin_id": PLUGIN_ID, "values": {}}
        assert initial.headers["cache-control"] == "private, no-store"
        assert initial.headers["x-content-type-options"] == "nosniff"

        saved = await client.put(SETTINGS, json={"token_lifetime_seconds": 900})
        assert saved.status_code == 200, saved.text
        # Reloading the page reads the document defaults and the saved values again.
        document = (await client.get(f"/api/plugins/{PLUGIN_ID}/ui")).json()
        assert document["settings"][0]["fields"][0]["default"] == 1200
        assert (await client.get(SETTINGS)).json()["values"] == {"token_lifetime_seconds": 900}

        edit = {"token_lifetime_seconds": 1500, "delivery": "push", "channels": ["webhook"]}
        assert (await client.put(SETTINGS, json=edit)).status_code == 200
        assert (await client.get(SETTINGS)).json()["values"] == edit
        assert (await client.put(SETTINGS, json={"note": ""})).status_code == 200
        assert (await client.get(SETTINGS)).json()["values"] == {**edit, "note": ""}

    # Another authorized administrator reloads the same installation configuration.
    async with client_for(stack, stack.bob) as client:
        assert (await client.get(SETTINGS)).json()["values"] == {**edit, "note": ""}


@pytest.mark.asyncio
async def test_write_only_secrets_and_unrepresentable_values_never_reach_the_browser(stack):
    grant(stack.boundary, "plugin.settings")
    grant(stack.boundary, "plugin.storage")
    stack.configuration.parent.mkdir(parents=True, exist_ok=True)
    # Stale configuration, such as values stored before a field became secret or
    # before an update removed an option, stays inside the runtime.
    stack.configuration.write_text(
        json.dumps(
            {
                "token_lifetime_seconds": True,
                "delivery": "sms",
                "channels": ["inbox", 7],
                "note": 42,
                "api_token": "stale-password-value",
                "webhook_url": "https://example.test/stale-secret",
                "internal_cursor": "runtime-only",
            }
        ),
        encoding="utf-8",
    )
    async with client_for(stack, stack.alice) as client:
        stored = await client.put(
            f"/api/plugins/{PLUGIN_ID}/secrets/api_token", json={"value": "current-secret"}
        )
        assert stored.status_code == 200, stored.text
        rejected = await client.put(SETTINGS, json={"api_token": "ordinary-write"})
        assert rejected.status_code == 422
        assert "secret settings" in rejected.json()["detail"]
        response = await client.get(SETTINGS)
        assert response.status_code == 200, response.text
        assert response.json()["values"] == {}
        for leaked in ("stale-password-value", "stale-secret", "current-secret", "runtime-only"):
            assert leaked not in response.text

        stack.configuration.write_text(
            json.dumps({"token_lifetime_seconds": float("nan"), "channels": ["inbox"]}),
            encoding="utf-8",
        )
        assert (await client.get(SETTINGS)).json()["values"] == {"channels": ["inbox"]}


@pytest.mark.asyncio
async def test_reading_saved_values_requires_the_same_user_scoped_grant_as_saving(stack):
    alice, bob = stack.alice, stack.bob
    stack.registry.settings(PLUGIN_ID, {"token_lifetime_seconds": 900})
    async with client_for(stack) as anonymous:
        assert (await anonymous.get(SETTINGS)).status_code == 401
    for user in (alice, bob):
        async with client_for(stack, user) as client:
            denied = await client.get(SETTINGS)
            assert denied.status_code == 403
            assert "900" not in denied.text
            assert (await client.put(SETTINGS, json={"note": "x"})).status_code == 403

    alice_grant = grant(stack.boundary, "plugin.settings", user_id=alice.id)
    alice.is_admin = False
    stack.boundary.session.commit()
    async with client_for(stack, alice) as client:
        # Settings authorization is grant-scoped, so a member holding the grant can
        # reload exactly what they are allowed to save.
        assert (await client.get(SETTINGS)).json()["values"] == {"token_lifetime_seconds": 900}
    async with client_for(stack, bob) as client:
        # Alice's user-scoped grant does not authorize another administrator.
        assert (await client.get(SETTINGS)).status_code == 403
        assert (await client.put(SETTINGS, json={"note": "x"})).status_code == 403

    alice_grant.revoked_at = 1
    stack.boundary.session.commit()
    async with client_for(stack, alice) as client:
        assert (await client.get(SETTINGS)).status_code == 403

    grant(stack.boundary, "plugin.settings")
    for user in (alice, bob):
        async with client_for(stack, user) as client:
            # An installation-wide grant authorizes members and administrators alike.
            response = await client.get(SETTINGS)
            assert response.json()["values"] == {"token_lifetime_seconds": 900}


@pytest.mark.asyncio
async def test_saved_values_follow_installation_lifecycle_and_runtime_availability(
    stack, monkeypatch
):
    grant(stack.boundary, "plugin.settings")
    stack.registry.settings(PLUGIN_ID, {"token_lifetime_seconds": 900})
    async with client_for(stack, stack.alice) as client:
        stack.registry._transition(PLUGIN_ID, enabled=False, status="disabled")
        assert (await client.get(SETTINGS)).status_code == 409
        stack.registry._transition(PLUGIN_ID, enabled=True, status="running")
        assert (await client.get(SETTINGS)).json()["values"] == {"token_lifetime_seconds": 900}
        assert (await client.get("/api/plugins/missing.plugin/settings")).status_code == 404
        monkeypatch.setattr(plugin_runtime, "client", PluginRuntimeClient("http://127.0.0.1:9"))
        assert (await client.get(SETTINGS)).status_code == 503


@pytest.mark.parametrize(
    ("field", "saved", "expected"),
    [
        ({"type": "number"}, "", ""),
        ({"type": "number", "required": True}, "", None),
        ({"type": "select", "options": [{"value": "a", "label": "A"}]}, "", ""),
        ({"type": "multiselect", "options": [{"value": "a", "label": "A"}]}, "", None),
        ({"type": "boolean"}, False, False),
        ({"type": "boolean"}, "false", None),
        ({"type": "textarea"}, "Line one\nLine two", "Line one\nLine two"),
    ],
)
def test_setting_values_mirror_what_the_declarative_renderer_can_show(field, saved, expected):
    document = PluginUiDocument.model_validate(
        {
            "plugin_id": PLUGIN_ID,
            "title": "Values",
            "settings": [
                {
                    "id": "general",
                    "title": "General",
                    "fields": [{"id": "value", "label": "Value", **field}],
                }
            ],
        }
    )
    values = filter_setting_values(document, {"value": saved})
    assert values == ({} if expected is None else {"value": expected})


def test_a_field_declared_secret_anywhere_stays_write_only():
    document = PluginUiDocument.model_validate(
        {
            "plugin_id": PLUGIN_ID,
            "title": "Values",
            "settings": [
                {
                    "id": "public",
                    "title": "Public",
                    "fields": [{"id": "key", "label": "Key", "type": "text"}],
                },
                {
                    "id": "private",
                    "title": "Private",
                    "fields": [{"id": "key", "label": "Key", "type": "text", "secret": True}],
                },
            ],
        }
    )
    assert filter_setting_values(document, {"key": "credential"}) == {}
