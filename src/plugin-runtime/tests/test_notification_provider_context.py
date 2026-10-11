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


def test_private_delivery_has_bound_endpoint_and_authenticated_actor_separately(provider):
    registry, installation, calls = provider
    destination = {"id": str(uuid4()), "revision": 2, "kind": "webhook"}
    owner, attempt = str(uuid4()), str(uuid4())
    assert registry.notification_delivery("contract.provider", "deliver",
        {"delivery": {"title": "Approved"}, "destination": destination},
        user_id=owner, installation_id=installation, attempt_id=attempt, server_revision=1) == {"success": True}
    assert calls == [{"delivery": {"title": "Approved"}, "_notification_context": {
        "operation": "deliver", "scope": "destination", "server_revision": 1, "installation_id": installation, "attempt_id": attempt,
        "destination": destination},
        "_plugin_context": {"user_id": owner, "is_admin": False}}]
    assert owner not in json.dumps(calls[0]["_notification_context"])
    assert owner not in json.dumps(calls[0]["delivery"])


def test_public_action_cannot_forge_private_context(provider):
    registry, _, calls = provider
    with pytest.raises(RuntimePolicyError, match="reserved"):
        registry.action("contract.provider", "deliver", {"_notification_context": {"operation": "deliver"}})
    assert calls == []


def test_configuration_receives_only_core_context_and_transient_values(provider):
    registry, installation, calls = provider
    destination = {"id": str(uuid4()), "revision": 2, "kind": "webhook"}
    context = {"operation": "configure", "scope": "destination", "provider_id": "contract.provider.webhook",
               "destination": destination}
    owner = str(uuid4())
    registry.notification_provider_action("contract.provider", "deliver", {"url": "own-secret"},
        user_id=owner, installation_id=installation, context=context)
    assert calls == [{"configuration": {"url": "own-secret"},
                     "_plugin_context": {"user_id": owner, "is_admin": False},
                     "_notification_context": {**context, "installation_id": installation}}]


@pytest.mark.parametrize("change", ["installation", "provider", "extra", "server_retire", "scope"])
def test_configuration_rejects_invalid_authority_before_execution(provider, change):
    registry, installation, calls = provider
    context = {"operation": "configure", "scope": "server", "provider_id": "contract.provider.webhook", "revision": 1}
    if change == "installation":
        installation = str(uuid4())
    elif change == "provider":
        context["provider_id"] = "another.plugin.webhook"
    elif change == "extra":
        context["user_id"] = str(uuid4())
    elif change == "server_retire":
        context["operation"] = "retire"
    elif change == "scope":
        context["scope"] = "admin"
    with pytest.raises(RuntimePolicyError):
        registry.notification_provider_action("contract.provider", "deliver", {}, user_id=str(uuid4()),
            installation_id=installation, context=context)
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
               user_id=str(uuid4()), installation_id=installation, attempt_id=str(uuid4()),
               **({"server_revision": 1} if change != "render" else {}))
    assert calls == []
