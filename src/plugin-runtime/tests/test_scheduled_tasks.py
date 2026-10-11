"""Installation-bound background dispatch reuses the isolated action boundary."""

import json
from uuid import uuid4

import pytest

from runtime import PluginRegistry, PluginSupervisor, RuntimePolicyError


@pytest.fixture
def scheduled_registry(tmp_path, monkeypatch, activate_registry):
    """A declarative package with the action runner replaced, never host imports."""
    root = tmp_path / "plugins"
    package = root / "example.tasks"
    package.mkdir(parents=True)
    document = {
        "api_contract_version": "1.1.0",
        "plugin_id": "example.tasks",
        "title": "Tasks",
        "actions": [
            {
                "id": "summary",
                "label": "Summary",
                "handler": "plugin:summary",
                "capability": {"name": "games.read", "version": 1},
            }
        ],
    }
    (package / "ui.json").write_text(json.dumps(document), encoding="utf-8")
    (package / "plugin.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "api_contract_version": "1.1.0",
                "plugin_id": "example.tasks",
                "name": "Tasks",
                "entrypoint": "plugin:main",
                "version": "1.1.0",
                "integrity": {"sha256": "0" * 64},
                "scheduled_tasks": [
                    {"id": "daily-summary", "name": "Summary", "action_id": "summary"}
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = PluginRegistry(root, PluginSupervisor(root=tmp_path / "processes"))
    activate_registry(registry, "example.tasks")
    actor = str(uuid4())
    registry._transition("example.tasks", user_id=actor)
    calls, authorizations = [], []
    monkeypatch.setattr(
        registry.supervisor,
        "execute",
        lambda spec, package, payload, **kwargs: (
            calls.append((json.loads(payload), kwargs)) or b'{"summary":"Finished"}'
        ),
    )
    monkeypatch.setattr(
        registry.supervisor,
        "_authorize_capability",
        lambda plugin_id, capability, **kwargs: authorizations.append(
            (plugin_id, capability, kwargs)
        ),
    )
    return (
        registry,
        actor,
        registry._state()["example.tasks"]["installation_id"],
        calls,
        authorizations,
        package,
    )


def test_task_rechecks_background_and_action_access_and_uses_existing_actor(scheduled_registry):
    registry, actor, identity, calls, authorizations, _ = scheduled_registry
    assert registry.scheduled_task(
        "example.tasks", "daily-summary", installation_id=identity, trigger="scheduled"
    ) == {"summary": "Finished"}
    assert calls == [
        ({"_scheduled_task": {"id": "daily-summary", "trigger": "scheduled"}}, {"user_id": actor})
    ]
    assert authorizations == [
        ("example.tasks", "tasks.background", {"user_id": actor}),
        ("example.tasks", "games.read", {"user_id": actor, "version": 1}),
    ]


@pytest.mark.parametrize("changes", [{"installation_id": str(uuid4())}, {"trigger": "arbitrary"}])
def test_task_rejects_stale_installation_or_unapproved_trigger(scheduled_registry, changes):
    registry, _, identity, calls, _, _ = scheduled_registry
    with pytest.raises(RuntimePolicyError):
        registry.scheduled_task(
            "example.tasks",
            "daily-summary",
            **{"installation_id": identity, "trigger": "manual", **changes},
        )
    assert not calls


def test_task_cannot_execute_an_undeclared_action_or_bypass_confirmation(scheduled_registry):
    registry, _, identity, calls, _, package = scheduled_registry
    with pytest.raises(KeyError):
        registry.scheduled_task(
            "example.tasks", "summary", installation_id=identity, trigger="manual"
        )
    document = json.loads((package / "ui.json").read_text())
    document["actions"][0]["confirmation"] = "Approve deletion"
    (package / "ui.json").write_text(json.dumps(document), encoding="utf-8")
    manifest = json.loads((package / "manifest.json").read_text())
    manifest["integrity"]["sha256"] = registry.digest(package)
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimePolicyError, match="without confirmation"):
        registry.scheduled_task(
            "example.tasks", "daily-summary", installation_id=identity, trigger="manual"
        )
    assert not calls


def test_task_rejects_missing_background_identity_and_grant(scheduled_registry, monkeypatch):
    registry, actor, identity, calls, _, _ = scheduled_registry
    registry._transition("example.tasks", user_id=None)
    with pytest.raises(RuntimePolicyError, match="identity is unavailable"):
        registry.scheduled_task(
            "example.tasks", "daily-summary", installation_id=identity, trigger="manual"
        )
    registry._transition("example.tasks", user_id=actor)

    def deny(*_args, **_kwargs):
        raise RuntimePolicyError("not granted")

    monkeypatch.setattr(registry.supervisor, "_authorize_capability", deny)
    with pytest.raises(RuntimePolicyError, match="not granted"):
        registry.scheduled_task(
            "example.tasks", "daily-summary", installation_id=identity, trigger="manual"
        )
    assert not calls


def test_server_task_cannot_override_worker_identity(scheduled_registry):
    registry, _, identity, calls, _, _ = scheduled_registry
    with pytest.raises(RuntimePolicyError, match="cannot override"):
        registry.scheduled_task(
            "example.tasks", "daily-summary", installation_id=identity,
            trigger="manual", user_id=str(uuid4()),
        )
    assert not calls


def test_personal_task_requires_explicit_target_and_uses_its_own_live_grants(scheduled_registry):
    registry, actor, identity, calls, authorizations, package = scheduled_registry
    manifest = json.loads((package / "manifest.json").read_text())
    manifest["api_contract_version"] = "1.1.11"
    manifest["scheduled_tasks"][0]["scope"] = "user"
    document = json.loads((package / "ui.json").read_text())
    document["api_contract_version"] = "1.1.11"
    (package / "ui.json").write_text(json.dumps(document), encoding="utf-8")
    manifest["integrity"]["sha256"] = registry.digest(package)
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimePolicyError, match="target account"):
        registry.scheduled_task(
            "example.tasks", "daily-summary", installation_id=identity, trigger="scheduled"
        )
    target = str(uuid4())
    assert target != actor
    registry.scheduled_task(
        "example.tasks", "daily-summary", installation_id=identity,
        trigger="scheduled", user_id=target,
    )
    assert calls[0][1] == {"user_id": target}
    assert calls[0][0]["_plugin_context"] == {"user_id": target, "is_admin": False}
    assert all(call[2]["user_id"] == target for call in authorizations)
