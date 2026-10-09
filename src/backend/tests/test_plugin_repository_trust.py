"""Cross-repository package-verification contract tests.

Set PLUGIN_REPOSITORY_PATH to a checkout of unnamed_tracking_app_plugins to
exercise the actual signed artifact, not a host-local fixture.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from src.plugin_api.installer import inspect_package
from src.plugin_api.publisher_trust import load_trusted_publishers
from src.plugin_api.updates import PackageVerificationError, PluginPackageVerifier


def _plugin_repository() -> Path:
    configured = os.getenv("PLUGIN_REPOSITORY_PATH")
    if not configured:
        pytest.skip("PLUGIN_REPOSITORY_PATH is required for cross-repository package verification")
    root = Path(configured)
    if not (root / "publishers" / "registry.json").is_file():
        pytest.skip("PLUGIN_REPOSITORY_PATH does not contain the publisher registry")
    return root


def test_all_reviewed_historical_archives_remain_trusted_by_bundled_host_policy() -> None:
    repository = _plugin_repository()
    publishers = load_trusted_publishers()
    pins = {pin for publisher in publishers.values() for pin in publisher.historical_package_sha256}
    assert len(pins) == 51
    archives = {
        hashlib.sha256(path.read_bytes()).hexdigest(): path
        for path in (repository / "dist").glob("*.utp")
    }
    assert pins <= archives.keys()
    verifier = PluginPackageVerifier(publishers)
    for digest in pins:
        package = inspect_package(archives[digest], verifier)
        assert package.trust.signature_verified, archives[digest].name
        assert package.package.archive_sha256 == digest


def test_current_published_packages_install_with_original_archive_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _plugin_repository()
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "plugin-runtime"))
    from runtime import PluginRegistry, PluginSupervisor

    catalogue = json.loads((repository / "list.json").read_text(encoding="utf-8"))
    verifier = PluginPackageVerifier(load_trusted_publishers())
    session_manager_seen = False
    for index, entry in enumerate(catalogue["plugins"]):
        path = repository / "dist" / entry["package"]["filename"]
        inspected = inspect_package(path, verifier)
        assert inspected.trust.signature_verified, path.name
        plugin_id = inspected.package.manifest.plugin_id
        if plugin_id == "example.self-service-session-manager":
            assert inspected.trust.publisher_channel == "official"
            session_manager_seen = True
        work = tmp_path / str(index)
        registry = PluginRegistry(work / "plugins", PluginSupervisor(work / "workers"))
        original = path.read_bytes()
        registry.install_package(original, path.name, installation_id=str(uuid4()))
        retained = base64.b64decode(registry.package_archive(plugin_id)["package"])
        assert retained == original
        assert registry.list()[0]["enabled"] is False
    assert session_manager_seen


def _write_modified_package(source: Path, destination: Path, *, unsigned: bool = False) -> None:
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(destination, "w") as output:
        for info in archive.infolist():
            content = archive.read(info)
            if info.filename == "manifest.json" and unsigned:
                manifest = json.loads(content)
                manifest["integrity"]["signature"] = None
                manifest["integrity"]["key_id"] = None
                content = json.dumps(manifest).encode("utf-8")
            elif info.filename.startswith("payload/") and not info.is_dir() and not unsigned:
                content += b"tampered"
            output.writestr(info, content)


def test_signed_plugin_repo_artifact_is_trusted_and_modified_artifacts_are_blocked(
    tmp_path: Path,
) -> None:
    plugin_repository = _plugin_repository()
    package = next((plugin_repository / "dist").glob("*.utp"))
    verifier = PluginPackageVerifier(
        publishers=load_trusted_publishers(plugin_repository / "publishers" / "registry.json")
    )

    verified = verifier.inspect(package)
    assert verified.manifest.plugin_id.startswith("example.")

    tampered = tmp_path / "tampered.utp"
    _write_modified_package(package, tampered)
    with pytest.raises(PackageVerificationError, match="integrity"):
        verifier.inspect(tampered)

    unsigned = tmp_path / "unsigned.utp"
    _write_modified_package(package, unsigned, unsigned=True)
    with pytest.raises(PackageVerificationError, match="unsigned"):
        verifier.inspect(unsigned)


def test_signed_plugin_repo_artifact_is_forwarded_only_after_verification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fastapi import UploadFile

    from src.api.routes import plugins
    from src.api.routes.plugin_manager import acquisition as plugin_acquisition
    from src.api.routes.plugin_manager import runtime as plugin_runtime
    from src.database.models import achievement as _achievement  # noqa: F401

    plugin_repository = _plugin_repository()
    package = next((plugin_repository / "dist").glob("*.utp"))
    verifier = PluginPackageVerifier(
        publishers=load_trusted_publishers(plugin_repository / "publishers" / "registry.json")
    )

    class RuntimeClient:
        package_bytes: bytes | None = None

        async def plugins(self):
            return []

        async def install_package(
            self,
            package_bytes: bytes,
            filename: str,
            *,
            installation_id: str,
            operation_id: str,
            source_metadata=None,
            trust_metadata=None,
        ) -> dict[str, str]:
            del installation_id
            self.package_bytes = package_bytes
            assert filename.endswith(".utp")
            return {"status": "installed", "operation_id": operation_id}

        async def finish_installation(self, plugin_id, operation_id, *, commit):
            assert commit is True

        async def finish_activation(self, plugin_id, operation_id, *, commit):
            assert commit

        async def prune_history(self, plugin_id, retain):
            assert retain >= 1

        async def start(self, plugin_id, user_id=None):
            self.started = plugin_id

        async def plugin_health(self, plugin_id):
            return self.started == plugin_id

    class FakeDb:
        def add_all(self, rows):
            self.rows = rows

        async def commit(self):
            pass

        async def rollback(self):
            pass

    runtime_client = RuntimeClient()
    monkeypatch.setattr(plugin_runtime, "client", runtime_client)
    monkeypatch.setattr(plugin_acquisition, "plugin_package_verifier", lambda: verifier)

    source = package.read_bytes()
    upload = UploadFile(file=io.BytesIO(source), filename=package.name)
    result = asyncio.run(plugins.install_plugin(upload, admin=object(), db=FakeDb()))
    assert result["status"] == "running"
    assert runtime_client.package_bytes == source
    forwarded = runtime_client.package_bytes

    tampered = tmp_path / "tampered.utp"
    _write_modified_package(package, tampered)
    upload = UploadFile(file=io.BytesIO(tampered.read_bytes()), filename=tampered.name)
    with pytest.raises(Exception) as error:
        asyncio.run(plugins.install_plugin(upload, admin=object(), db=FakeDb()))
    assert getattr(error.value, "status_code", None) == 400
    assert runtime_client.package_bytes == forwarded
