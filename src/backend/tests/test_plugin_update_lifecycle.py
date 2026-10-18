"""Integration-level regressions for update discovery and permission review."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import plugins
from src.api.routes.plugin_manager import acquisition as plugin_acquisition
from src.api.routes.plugin_manager import catalogues as plugin_catalogues
from src.api.routes.plugin_manager import runtime as plugin_runtime
from src.database.models import achievement as _achievement  # noqa: F401
from src.plugin_api.installer import inspect_package
from src.plugin_api.updates import PluginPackageVerifier


def _unsigned_update(path: Path) -> None:
    payload = b"def main():\n    pass\n"
    digest = hashlib.sha256()
    digest.update(b"plugin.py\0")
    digest.update(payload)
    digest.update(b"\0")
    manifest = {
        "api_contract_version": "1.1.0",
        "manifest_version": 1,
        "plugin_id": "example.update",
        "name": "Update Example",
        "version": "2.0.0",
        "entrypoint": "plugin:main",
        "sdk_version_range": "*",
        "application_version_range": "*",
        "capabilities": [{"name": "api.full", "version": 1}],
        "permissions": [
            {
                "capability": {"name": "api.full", "version": 1},
                "rationale": "Exercise update permission review.",
            }
        ],
        "dependencies": [],
        "integrity": {"sha256": digest.hexdigest()},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("payload/plugin.py", payload)


@pytest.mark.asyncio
async def test_catalogue_update_detection_includes_release_notes(monkeypatch) -> None:
    async def fake_catalog(*, source, user):
        assert source == "https://catalogue.example/list.json"
        assert user is not None
        return [
            plugins.PluginCatalogEntry(
                plugin_id="example.update",
                name="Update Example",
                version="2.0.0",
                url="https://packages.example/update.utp",
                release_notes="Security fixes",
            )
        ]

    monkeypatch.setattr(plugin_catalogues, "plugin_catalog", fake_catalog)
    result = await plugins._check_plugin_update(
        {
            "plugin_id": "example.update",
            "version": "1.0.0",
            "source": {
                "type": "catalogue",
                "catalogue_url": "https://catalogue.example/list.json",
            },
        },
        SimpleNamespace(),
    )
    assert result["update_available"] is True
    assert result["available_version"] == "2.0.0"
    assert result["release_notes"] == "Security fixes"


@pytest.mark.asyncio
async def test_unverified_update_re_reviews_every_permission(monkeypatch, tmp_path: Path) -> None:
    package = tmp_path / "update.bin"
    _unsigned_update(package)
    inspected = inspect_package(package, PluginPackageVerifier(require_signature=False))
    installation_id = uuid4()

    class Runtime:
        async def plugins(self):
            return [
                {
                    "plugin_id": "example.update",
                    "version": "1.0.0",
                    "installation_id": str(installation_id),
                    "trust": {"status": "unsigned", "publisher_key_id": None},
                    "permission_refs": [{"name": "api.full", "version": 1}],
                    "dependencies": [],
                }
            ]

    class Result:
        def all(self):
            return [("api.full", 1)]

    class Db:
        async def execute(self, statement):
            del statement
            return Result()

    monkeypatch.setattr(plugin_runtime, "client", Runtime())
    _, _, delta, _, can_retain = await plugins._update_context(
        "example.update",
        inspected,
        Db(),
    )
    assert can_retain is False
    assert [item.name.value for item in delta.newly_requested_grants] == ["api.full"]
    assert delta.existing_grants == ()


@pytest.mark.asyncio
async def test_update_notification_uses_controller(monkeypatch) -> None:
    admin_id = uuid4()

    class Db:
        def __init__(self) -> None:
            self.calls = 0
            self.rows = []

        async def scalars(self, statement):
            del statement
            self.calls += 1
            return [admin_id] if self.calls == 1 else []

        def add(self, row):
            self.rows.append(row)

    db = Db()
    from unittest.mock import AsyncMock

    emit = AsyncMock(return_value=[uuid4()])
    monkeypatch.setattr(plugin_catalogues, "emit_legacy_rows", emit)
    await plugins._notify_plugin_update(
        db,
        {
            "plugin_id": "example.update",
            "current_version": "1.0.0",
            "available_version": "2.0.0",
            "update_available": True,
        },
    )
    assert emit.await_count == 1
    assert emit.await_args.args[:2] == (db, admin_id)
    notices = emit.await_args.args[2]
    assert len(notices) == 1
    notice = notices[0]
    assert notice["kind"] == "plugin_update"
    assert notice["title"] == "Plugin updates: 1 plugin(s) updated or need attention"
    assert "example.update (1.0.0 → 2.0.0)" in notice["body"]
    # Without a configured public URL the body must not show a dead relative path.
    assert "/settings?area=administration" not in notice["body"]
    assert "public app URL" in notice["body"]
    assert notice["content"].actions[0].target.path.startswith("/settings?area=administration")
    assert notice["dedupe_key"].startswith("plugin-update-batch:")


@pytest.mark.asyncio
async def test_four_plugin_updates_are_one_consolidated_notification(monkeypatch) -> None:
    admin_id = uuid4()

    class Db:
        async def scalars(self, statement):
            del statement
            return [admin_id]

    db = Db()
    from unittest.mock import AsyncMock

    emit = AsyncMock(return_value=[uuid4()])
    monkeypatch.setattr(plugin_catalogues, "emit_legacy_rows", emit)
    monkeypatch.setattr(plugin_catalogues.settings, "PUBLIC_APP_URL", "https://tracking.example")
    updates = [
        {
            "plugin_id": f"example.plugin{i}",
            "current_version": f"1.{i}.0",
            "available_version": f"2.{i}.0",
            "update_available": True,
            "status": "available",
        }
        for i in range(4)
    ]

    await plugin_catalogues.notify_plugin_updates(db, updates)

    assert emit.await_count == 1
    notices = emit.await_args.args[2]
    assert len(notices) == 1
    body = notices[0]["body"]
    for update in updates:
        assert update["plugin_id"] in body
        assert f"{update['current_version']} → {update['available_version']}" in body
    assert notices[0]["dedupe_key"].startswith("plugin-update-batch:")
    assert "https://tracking.example/settings?area=administration&section=plugins" in body
    content = notices[0]["content"]
    markdown = content.blocks[0].markdown
    assert "Manage plugins [here](https://tracking.example/settings?area=administration" in markdown
    assert "[example.plugin0](https://tracking.example/settings?area=administration" in markdown
    assert "plugin=example.plugin0" in markdown
    assert [action.id for action in content.actions] == [
        "manage_plugins",
        "plugin_1",
        "plugin_2",
        "plugin_3",
        "plugin_4",
    ]


@pytest.mark.asyncio
async def test_batch_links_the_changelog_reason(monkeypatch) -> None:
    class Db:
        async def scalars(self, statement):
            del statement
            return [uuid4()]

    from unittest.mock import AsyncMock

    emit = AsyncMock(return_value=[uuid4()])
    monkeypatch.setattr(plugin_catalogues, "emit_legacy_rows", emit)
    monkeypatch.setattr(plugin_catalogues.settings, "PUBLIC_APP_URL", "")
    await plugin_catalogues.notify_plugin_updates(
        Db(),
        [
            {
                "plugin_id": "example.one",
                "current_version": "1.0.0",
                "available_version": "2.0.0",
                "update_available": True,
                "status": "available",
                "changelog_url": "https://example.com/changes",
            }
        ],
    )
    markdown = emit.await_args.args[2][0]["content"].blocks[0].markdown
    assert "([release notes](https://example.com/changes))" in markdown
    assert "example.one" in markdown and "](/settings" not in markdown


@pytest.mark.asyncio
async def test_batch_summarizes_mixed_automatic_update_outcomes(monkeypatch) -> None:
    admin_id = uuid4()

    class Db:
        async def scalars(self, statement):
            del statement
            return [admin_id]

    from unittest.mock import AsyncMock

    emit = AsyncMock(return_value=[uuid4()])
    monkeypatch.setattr(plugin_catalogues, "emit_legacy_rows", emit)
    await plugin_catalogues.notify_plugin_updates(
        Db(),
        [
            {
                "plugin_id": "example.updated",
                "current_version": "1.0.0",
                "available_version": "2.0.0",
                "status": "updated",
            },
            {
                "plugin_id": "example.failed",
                "current_version": "3.0.0",
                "available_version": "4.0.0",
                "status": "failed",
            },
            {
                "plugin_id": "example.pending",
                "current_version": "5.0.0",
                "available_version": "6.0.0",
                "status": "available",
                "reason": "Global automatic updates are disabled.",
            },
        ],
    )

    body = emit.await_args.args[2][0]["body"]
    assert "Updated automatically: example.updated (1.0.0 → 2.0.0)" in body
    assert "Automatic update failed: example.failed (3.0.0 → 4.0.0)" in body
    assert "Update still available: example.pending (5.0.0 → 6.0.0)" in body
    assert "Global automatic updates are disabled." in body


@pytest.mark.asyncio
async def test_unchanged_plugin_update_batch_is_not_notified_twice(monkeypatch) -> None:
    records: dict[str, dict[str, object]] = {}

    class Store:
        def read(self):
            return {"plugins": records}

        def patch(self, plugin_id: str, **changes):
            records.setdefault(plugin_id, {}).update(changes)

    store = Store()
    monkeypatch.setattr(plugin_catalogues, "manager_state", lambda: store)
    batch = [
        {
            "plugin_id": "example.one",
            "current_version": "1.0.0",
            "available_version": "2.0.0",
            "status": "available",
        },
        {
            "plugin_id": "example.two",
            "current_version": "3.0.0",
            "available_version": "4.0.0",
            "status": "available",
        },
    ]

    assert plugin_catalogues.plugin_update_batch_changed(batch)
    plugin_catalogues.mark_plugin_update_batch_notified(batch)
    assert not plugin_catalogues.plugin_update_batch_changed(batch)

    changed = [{**batch[0], "available_version": "2.1.0"}, batch[1]]
    assert plugin_catalogues.plugin_update_batch_changed(changed)

    legacy = {
        "plugin_id": "example.legacy",
        "current_version": "1.0.0",
        "available_version": "2.0.0",
        "update_available": True,
    }
    store.patch("example.legacy", available_update=legacy)
    plugin_catalogues.adopt_legacy_update_notification("example.legacy", legacy, legacy)
    assert not plugin_catalogues.plugin_update_batch_changed([{**legacy, "status": "available"}])


@pytest.mark.asyncio
async def test_inline_changelog_is_returned_before_update(monkeypatch) -> None:
    class Runtime:
        async def plugins(self):
            return [{"plugin_id": "example.update", "version": "1.0.0"}]

    async def fake_check(plugin, admin):
        del plugin, admin
        return {
            "available_version": "2.0.0",
            "release_notes": "Security fixes",
        }

    monkeypatch.setattr(plugin_runtime, "client", Runtime())
    monkeypatch.setattr(plugin_catalogues, "check_plugin_update", fake_check)
    changelog = await plugins.plugin_changelog("example.update", SimpleNamespace())
    assert changelog == {
        "plugin_id": "example.update",
        "version": "2.0.0",
        "format": "markdown",
        "source": "catalogue",
        "body": "Security fixes",
    }


@pytest.mark.asyncio
async def test_catalogue_failure_updates_persistent_status(monkeypatch, tmp_path: Path) -> None:
    registry = tmp_path / "catalogues.json"
    monkeypatch.setenv("PLUGIN_CATALOGUE_REGISTRY", str(registry))
    plugins._catalogue_store_for.cache_clear()
    store = plugins._catalogue_store()
    catalogue = store.add(
        name="Broken",
        url="https://catalogue.example/list.json",
        enabled=True,
        priority=10,
    )

    async def fail_download(url, *, json_document=False):
        del url, json_document
        raise HTTPException(status_code=502, detail="network failure")

    monkeypatch.setattr(plugin_acquisition, "download_remote_file", fail_download)
    with pytest.raises(HTTPException):
        await plugins.plugin_catalog(
            source="https://catalogue.example/list.json",
            user=SimpleNamespace(),
        )
    persisted = next(item for item in store.list() if item["id"] == catalogue["id"])
    assert persisted["last_error"] == "network failure"
