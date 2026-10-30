"""Host access snapshots are local to one actor/action and observe revocation."""

from access import RuntimeAccess


def snapshot(installation="installation", user="user", duration=5):
    return {"installation_id": installation, "user_id": user,
            "capabilities": ["plugin.storage", "plugin.settings"],
            "expires_in_seconds": duration}


def test_snapshots_cannot_outlive_an_action_or_cross_actor_installation_or_capability():
    access = RuntimeAccess()
    previous = access.begin("plugin", "installation", "user")
    access.accept("plugin", "installation", "user", snapshot(), access.revision("plugin", "user"))
    assert access.allows("plugin", "installation", "user", "plugin.storage", 1)
    assert not access.allows("plugin", "other", "user", "plugin.storage", 1)
    assert not access.allows("plugin", "installation", "other", "plugin.storage", 1)
    assert not access.allows("other", "installation", "user", "plugin.storage", 1)
    assert not access.allows("plugin", "installation", "user", "network.outbound", 1)
    assert not access.allows("plugin", "installation", "user", "plugin.storage", 2)
    access.end(previous)
    assert not access.allows("plugin", "installation", "user", "plugin.storage", 1)


def test_revocation_and_expiry_invalidate_local_authorization(monkeypatch):
    import access as module
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    access = RuntimeAccess()
    access.begin("plugin", "installation", "user")
    access.accept("plugin", "installation", "user", snapshot(), access.revision("plugin", "user"))
    access.invalidate("plugin", "other")
    assert access.allows("plugin", "installation", "user", "plugin.storage", 1)
    access.invalidate("plugin", "user")
    assert not access.allows("plugin", "installation", "user", "plugin.storage", 1)
    access.accept("plugin", "installation", "user", snapshot(), access.revision("plugin", "user"))
    now[0] += 5
    assert not access.allows("plugin", "installation", "user", "plugin.storage", 1)


def test_racing_invalidation_or_invalid_snapshot_cannot_create_access():
    access = RuntimeAccess()
    access.begin("plugin", "installation", "user")
    before = access.revision("plugin", "user")
    access.invalidate("plugin")
    for value in [snapshot(), snapshot(user="other"), snapshot(duration=60),
                  snapshot(duration=True), {**snapshot(), "capabilities": ["network.outbound"]}]:
        access.accept("plugin", "installation", "user", value, before)
        assert not access.allows("plugin", "installation", "user", "plugin.storage", 1)
