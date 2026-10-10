"""Only the private core delivery boundary can issue generic provider context."""

import json
from uuid import uuid4

import pytest
from runtime import PluginRegistry, PluginSupervisor, RuntimePolicyError


@pytest.fixture
def provider(tmp_path, monkeypatch, activate_registry):
    root = tmp_path / "plugins"
    package = root / "contract.provider"
    package.mkdir(parents=True)
    (package / "plugin.py").write_text("", encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps({
        "api_contract_version": "1.1.5", "plugin_id": "contract.provider",
        "entrypoint": "plugin:main",
        "capabilities": [{"name": "notification_providers.deliver", "version": 1}],
    }), encoding="utf-8")
    (package / "ui.json").write_text(json.dumps({
        "api_contract_version": "1.1.5", "plugin_id": "contract.provider",
        "actions": [{"id": "deliver", "handler": "plugin:deliver"}],
    }), encoding="utf-8")
    registry = PluginRegistry(root, PluginSupervisor(root=tmp_path / "work"))
    registry._save_state({"contract.provider": {"enabled": True}})
    activate_registry(registry, "contract.provider")
    monkeypatch.setattr(registry.supervisor, "_authorize_capability", lambda *_args, **_kwargs: None)
    calls = []
    monkeypatch.setattr(registry.supervisor, "execute", lambda _spec, _package, payload, **_kw:
                        calls.append(json.loads(payload)) or b'{"success":true}')
    return registry, registry._item(package)["installation_id"], calls


def test_private_delivery_context_contains_bound_endpoint_and_not_owner(provider):
    registry, installation, calls = provider
    destination = {"id": str(uuid4()), "revision": 2, "kind": "webhook"}
    owner, attempt = str(uuid4()), str(uuid4())
    assert registry.notification_delivery("contract.provider", "deliver",
        {"delivery": {"title": "Approved"}, "destination": destination},
        user_id=owner, installation_id=installation, attempt_id=attempt) == {"success": True}
    assert calls == [{"delivery": {"title": "Approved"}, "_notification_context": {
        "operation": "deliver", "installation_id": installation, "attempt_id": attempt,
        "destination": destination}}]
    assert owner not in json.dumps(calls)


def test_public_action_cannot_forge_private_context(provider):
    registry, _, calls = provider
    with pytest.raises(RuntimePolicyError, match="reserved"):
        registry.action("contract.provider", "deliver", {"_notification_context": {"operation": "deliver"}})
    assert calls == []


@pytest.mark.parametrize("change", ["installation", "revision", "kind", "extra", "render"])
def test_invalid_generic_envelopes_never_execute_plugin(provider, change):
    registry, installation, calls = provider
    destination = {"id": str(uuid4()), "revision": 1, "kind": "webhook"}
    if change == "installation":
        installation = str(uuid4())
    elif change == "revision":
        destination["revision"] = True
    elif change == "kind":
        destination["kind"] = "../foreign"
    elif change == "extra":
        destination["user_id"] = str(uuid4())
    method = registry.notification_layout if change == "render" else registry.notification_delivery
    with pytest.raises(RuntimePolicyError):
        method("contract.provider", "deliver", {"delivery": {}, "destination": destination},
               user_id=str(uuid4()), installation_id=installation, attempt_id=str(uuid4()))
    assert calls == []
