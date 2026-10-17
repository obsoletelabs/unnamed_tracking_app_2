"""Persisted declarative settings are readable without releasing write-only secrets."""

import json
import threading
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from runtime import (
    PluginRegistry,
    PluginSupervisor,
    RuntimeHandler,
    RuntimePolicyError,
    RuntimeServer,
)
from test_runtime import _package_bytes

PLUGIN_ID = "example.upload"
UI_DOCUMENT = {
    "schema_version": "v1",
    "api_contract_version": "1.1.0",
    "plugin_id": PLUGIN_ID,
    "title": "Settings contract",
    "settings": [
        {
            "id": "recovery",
            "title": "Recovery",
            "fields": [
                {
                    "id": "token_lifetime_seconds",
                    "label": "Link lifetime",
                    "type": "number",
                    "default": 1200,
                },
                {"id": "label", "label": "Label", "type": "text"},
                {
                    "id": "api_token",
                    "label": "API token",
                    "type": "password",
                    "secret": True,
                },
                {"id": "webhook", "label": "Webhook", "type": "text", "secret": True},
            ],
        }
    ],
    "pages": [{"id": "configure", "title": "Configure", "settings": ["recovery"]}],
}


def _registry(tmp_path):
    supervisor = PluginSupervisor(tmp_path / "work", tmp_path / "storage")
    registry = PluginRegistry(tmp_path / "plugins", supervisor)
    registry.install_package(
        _package_bytes(), "settings.utp", installation_id=str(uuid.uuid4())
    )
    package = registry.package(PLUGIN_ID)[0]
    (package / "ui.json").write_text(json.dumps(UI_DOCUMENT), encoding="utf-8")
    return registry


def test_saved_settings_follow_reload_and_edit_cycles(tmp_path):
    registry = _registry(tmp_path)
    assert registry.saved_settings(PLUGIN_ID) == {}
    registry.settings(PLUGIN_ID, {"token_lifetime_seconds": 900})
    assert registry.saved_settings(PLUGIN_ID) == {"token_lifetime_seconds": 900}
    registry.settings(PLUGIN_ID, {"token_lifetime_seconds": 1500, "label": "Ops"})
    assert registry.saved_settings(PLUGIN_ID) == {
        "token_lifetime_seconds": 1500,
        "label": "Ops",
    }
    with pytest.raises(RuntimePolicyError, match="secret settings"):
        registry.settings(PLUGIN_ID, {"api_token": "never-stored"})


def test_saved_settings_omit_secret_and_undeclared_stored_values(tmp_path):
    registry = _registry(tmp_path)
    # Configuration written before a field became secret, or migrated from a legacy
    # package-local file, must stay inside the runtime.
    configuration = tmp_path / ".configuration" / f"{PLUGIN_ID}.json"
    configuration.parent.mkdir(parents=True)
    configuration.write_text(
        json.dumps(
            {
                "token_lifetime_seconds": 900,
                "api_token": "stale-password-value",
                "webhook": "https://example.test/stale-secret",
                "internal_cursor": "runtime-only",
            }
        ),
        encoding="utf-8",
    )
    assert registry.saved_settings(PLUGIN_ID) == {"token_lifetime_seconds": 900}


def test_saved_settings_http_route_is_authenticated(tmp_path, monkeypatch):
    token = "settings-runtime-test-token-" + "x" * 32
    monkeypatch.setenv("PLUGIN_RUNTIME_TOKEN", token)
    registry = _registry(tmp_path)
    registry.settings(PLUGIN_ID, {"token_lifetime_seconds": 900})
    (tmp_path / ".configuration" / f"{PLUGIN_ID}.json").write_text(
        json.dumps({"token_lifetime_seconds": 900, "webhook": "stale-secret"}),
        encoding="utf-8",
    )
    server = RuntimeServer(("127.0.0.1", 0), RuntimeHandler)
    server.registry = registry
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    headers = {"X-Plugin-Runtime-Token": token}
    try:
        with pytest.raises(HTTPError) as unauthorized:
            urlopen(Request(f"{base_url}/plugins/{PLUGIN_ID}/settings"), timeout=5)
        assert unauthorized.value.code == 401
        with urlopen(
            Request(f"{base_url}/plugins/{PLUGIN_ID}/settings", headers=headers),
            timeout=5,
        ) as response:
            body = response.read()
        assert json.loads(body) == {"values": {"token_lifetime_seconds": 900}}
        assert b"stale-secret" not in body
        with pytest.raises(HTTPError) as missing:
            urlopen(
                Request(f"{base_url}/plugins/example.missing/settings", headers=headers),
                timeout=5,
            )
        assert missing.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
