"""Host-authorised story actions: the runtime repeats what it can check and adds private context."""

import json
from uuid import uuid4

import pytest
from runtime import PluginRegistry, PluginSupervisor, RuntimePolicyError


@pytest.fixture
def actions(tmp_path, monkeypatch, activate_registry):
    root = tmp_path / "plugins"
    package = root / "contract.actions"
    package.mkdir(parents=True)
    (package / "plugin.py").write_text("", encoding="utf-8")

    def declare(capabilities):
        (package / "manifest.json").write_text(json.dumps({
            "api_contract_version": "1.1.14", "plugin_id": "contract.actions",
            "entrypoint": "plugin:main",
            "capabilities": [{"name": name, "version": 1} for name in capabilities],
        }), encoding="utf-8")

    declare(["notifications.actions"])
    (package / "ui.json").write_text(json.dumps({
        "api_contract_version": "1.1.14", "plugin_id": "contract.actions",
        "actions": [{"id": "apply", "handler": "plugin:apply"}],
    }), encoding="utf-8")
    registry = PluginRegistry(root, PluginSupervisor(root=tmp_path / "work"))
    registry._save_state({"contract.actions": {"enabled": True}})
    activate_registry(registry, "contract.actions")
    granted = []
    monkeypatch.setattr(registry.supervisor, "_authorize_capability",
                        lambda _id, name, **_kw: granted.append(name))
    calls, output = [], [b'{"message": "Installed 2.0"}']
    monkeypatch.setattr(registry.supervisor, "execute", lambda _spec, _package, payload, **_kw:
                        calls.append(json.loads(payload)) or output[0])
    return registry, registry._item(package)["installation_id"], calls, granted, declare, output


def invoke(registry, installation, **changes):
    arguments = {"user_id": str(uuid4()), "installation_id": installation,
                 "story_id": str(uuid4()), "revision": 3, **changes}
    return registry.notification_story_action("contract.actions", "apply", {"channel": "stable"},
                                              **arguments)


def test_handler_receives_private_story_context_and_the_host_input(actions):
    registry, installation, calls, granted, _, _ = actions
    owner, story = str(uuid4()), str(uuid4())
    assert invoke(registry, installation, user_id=owner, story_id=story) == {
        "ok": True, "message": "Installed 2.0"}
    assert granted == ["notifications.actions"]
    assert calls == [{
        "channel": "stable",
        "_plugin_context": {"user_id": owner, "is_admin": False},
        "_notification_context": {"operation": "story_action", "story_id": story,
                                  "revision": 3, "installation_id": installation},
    }]


def test_unbounded_or_unprintable_messages_are_trimmed_or_dropped(actions):
    registry, installation, _, _, _, output = actions
    output[0] = json.dumps({"message": "x" * 5000}).encode()
    assert len(invoke(registry, installation)["message"]) == 300
    output[0] = json.dumps({"message": "a\x00b"}).encode()
    assert invoke(registry, installation)["message"] is None
    output[0] = b""
    assert invoke(registry, installation) == {"ok": True, "message": None}


@pytest.mark.parametrize("change", [
    {"installation_id": str(uuid4())},
    {"revision": 0},
    {"revision": True},
    {"user_id": "not-a-uuid"},
    {"story_id": "not-a-uuid"},
])
def test_invalid_authority_is_rejected_before_any_plugin_code_runs(actions, change):
    registry, installation, calls, _, _, _ = actions
    with pytest.raises((RuntimePolicyError, ValueError)):
        registry.notification_story_action(
            "contract.actions", "apply", {},
            **({"user_id": str(uuid4()), "installation_id": installation,
                "story_id": str(uuid4()), "revision": 1} | change))
    assert calls == []


@pytest.mark.parametrize("values", [{"_notification_context": {}}, {"_plugin_context": {}},
                                    {f"k{i}": "v" for i in range(9)}])
def test_input_cannot_replace_reserved_context_or_exceed_its_bounds(actions, values):
    registry, installation, calls, _, _, _ = actions
    with pytest.raises(RuntimePolicyError):
        registry.notification_story_action(
            "contract.actions", "apply", values, user_id=str(uuid4()),
            installation_id=installation, story_id=str(uuid4()), revision=1)
    assert calls == []


def test_a_plugin_that_does_not_declare_the_capability_cannot_run_actions(actions):
    registry, installation, calls, _, declare, _ = actions
    declare(["notification_providers.deliver"])
    with pytest.raises(RuntimePolicyError, match="notification actions"):
        invoke(registry, installation)
    assert calls == []


def test_undeclared_handlers_are_not_reachable(actions):
    registry, installation, calls, _, _, _ = actions
    with pytest.raises(KeyError):
        registry.notification_story_action(
            "contract.actions", "missing", {}, user_id=str(uuid4()),
            installation_id=installation, story_id=str(uuid4()), revision=1)
    assert calls == []


def test_public_actions_cannot_forge_the_story_context(actions):
    registry, _, calls, _, _, _ = actions
    with pytest.raises(RuntimePolicyError, match="reserved"):
        registry.action("contract.actions", "apply",
                        {"_notification_context": {"operation": "story_action"}})
    assert calls == []
