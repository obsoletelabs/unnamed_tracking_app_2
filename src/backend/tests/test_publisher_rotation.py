"""Key rotation must preserve only reviewed archive bytes, never claimed dates."""

from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.plugin_api import updates
from src.plugin_api.installer import inspect_package
from src.plugin_api.publisher_trust import PublisherTrustError, load_trusted_publishers
from src.plugin_api.updates import (
    PackageFormatError,
    PackageVerificationError,
    PluginPackageVerifier,
    TrustedPublisher,
    canonical_payload_digest,
)
from tests.test_publisher_trust import registry_entry, write_registry

CUTOFF = datetime(2026, 10, 7, 16, tzinfo=UTC)


def signed_archive(tmp_path: Path, *, version: int = 2) -> tuple[Path, dict[str, object]]:
    key = Ed25519PrivateKey.generate()
    manifest = {
        "plugin_id": "example.test",
        "name": "Rotation test",
        "version": "1.0.0",
        "entrypoint": "plugin:main",
        "sdk_version_range": "*",
        "application_version_range": "*",
    }
    payload = {
        "plugin.py": b"def main(): pass\n",
        "distribution.json": json.dumps(
            {
                "schema_version": 1,
                "version": "1.0.0",
                "automatic_update": False,
                "tags": [],
                "built_at": "2018-01-01T00:00:00Z",
            }
        ).encode(),
    }
    if version == 2:
        payload["package-signature-v2.json"] = json.dumps(
            {"schema_version": 2, "key_id": "official-test", "manifest": manifest}
        ).encode()
    digest = canonical_payload_digest(list(payload.items()))
    claim_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    signature = base64.b64encode(key.sign(f"plugin-package-v{version}:{digest}".encode())).decode()
    package = tmp_path / "rotation.utp"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    **manifest,
                    "integrity": {
                        "sha256": digest,
                        "key_id": "official-test",
                        "signature": ("v2:" if version == 2 else "") + signature,
                    },
                }
            ),
        )
        for name, content in payload.items():
            archive.writestr("payload/" + name, content)
    entry = registry_entry(
        key,
        not_before="2010-01-01T00:00:00Z",
        not_after="2019-01-01T00:00:00Z",
        legacy_manifest_hashes={digest: [claim_hash]},
        historical_package_sha256=[hashlib.sha256(package.read_bytes()).hexdigest()],
    )
    return package, entry


@pytest.mark.parametrize("offset,allowed", [(-1, True), (0, False), (1, False)])
def test_key_cutoff_is_exclusive(offset: int, allowed: bool) -> None:
    publisher = TrustedPublisher("test", b"x" * 32, not_after=CUTOFF)
    assert (
        publisher.allows_plugin("example.test", now=CUTOFF + timedelta(seconds=offset)) is allowed
    )


def test_history_cannot_bypass_start_revocation_or_scope() -> None:
    publisher = TrustedPublisher(
        "test",
        b"x" * 32,
        plugin_id_prefixes=("official.",),
        plugin_ids=("example.self-service-session-manager",),
        not_before=CUTOFF - timedelta(days=1),
        not_after=CUTOFF,
        historical_package_sha256=frozenset({"a" * 64}),
    )
    assert publisher.allows_package("official.test", "a" * 64, now=CUTOFF)
    assert not publisher.allows_package("official.test", "b" * 64, now=CUTOFF)
    assert not publisher.allows_package("official.test", "a" * 64, now=CUTOFF - timedelta(days=2))
    assert not replace(publisher, status="revoked").allows_package(
        "official.test", "a" * 64, now=CUTOFF
    )
    assert publisher.allows_package("example.self-service-session-manager", "a" * 64, now=CUTOFF)
    for plugin_id in ("example.other", "example.self-service-session-manager-extra"):
        assert not publisher.allows_package(plugin_id, "a" * 64, now=CUTOFF)


@pytest.mark.parametrize(
    "policy",
    [
        {"not_before": "2026-01-01"},
        {"not_after": "2026-01-01T00:00:00"},
        {"not_after": "bad"},
        {"not_after": 123},
        {"not_after": []},
        {"not_before": "2026-01-02T00:00:00Z", "not_after": "2026-01-01T00:00:00Z"},
        {"not_before": "2026-01-01T00:00:00Z", "not_after": "2026-01-01T00:00:00Z"},
        {"plugin_ids": "example.test"},
        {"plugin_ids": ["example.test", "example.test"]},
        {"plugin_ids": ["example.*"]},
        {"plugin_ids": [None]},
        {"historical_package_sha256": "a" * 64},
        {"historical_package_sha256": ["a" * 64, "a" * 64]},
        {"historical_package_sha256": ["a" * 63]},
        {"historical_package_sha256": [None]},
    ],
)
def test_registry_rejects_invalid_rotation_policy(tmp_path: Path, policy: dict) -> None:
    registry = tmp_path / "registry.json"
    write_registry(registry, registry_entry(Ed25519PrivateKey.generate(), **policy))
    with pytest.raises(PublisherTrustError):
        load_trusted_publishers(registry)


def test_registry_normalizes_offset_timestamps_and_exact_only_scope(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    write_registry(
        registry,
        registry_entry(
            Ed25519PrivateKey.generate(),
            not_before="2026-10-08T00:00:00+08:00",
            plugin_id_prefixes=[],
            plugin_ids=["example.test"],
        ),
    )
    publisher = load_trusted_publishers(registry)["official-test"]
    assert publisher.not_before == CUTOFF
    assert publisher.allows_plugin("example.test", now=CUTOFF)
    assert not publisher.allows_plugin("example.test-extra", now=CUTOFF)


@pytest.mark.parametrize("version", [1, 2])
def test_expired_key_preserves_only_exact_history_and_blocks_backdating(
    tmp_path: Path, version: int
) -> None:
    package, entry = signed_archive(tmp_path, version=version)
    registry = tmp_path / "registry.json"
    write_registry(registry, entry)
    verifier = PluginPackageVerifier(load_trusted_publishers(registry))
    inspected = inspect_package(package, verifier)
    assert inspected.trust.signature_verified
    assert inspected.package.archive_sha256 == hashlib.sha256(package.read_bytes()).hexdigest()
    verifier.extract(inspected.package, tmp_path / "installed")
    assert (tmp_path / "installed" / "plugin.py").is_file()

    # A ZIP comment preserves the signed payload and backdated distribution metadata.
    with zipfile.ZipFile(package, "a") as archive:
        archive.comment = b"new archive under the expired key"
    with pytest.raises(PackageVerificationError, match="publisher"):
        verifier.inspect(package)
    # Installation preview/consent cannot downgrade an expired signer to an unknown-key warning.
    with pytest.raises(PackageVerificationError, match="publisher"):
        inspect_package(package, verifier)


@pytest.mark.parametrize(
    "policy",
    [
        {"status": "revoked"},
        {"plugin_id_prefixes": ["official."]},
        {"not_before": "2100-01-01T00:00:00Z", "not_after": None},
        {"legacy_manifest_hashes": {}},
    ],
)
def test_pinned_history_still_requires_all_trust_checks(tmp_path: Path, policy: dict) -> None:
    package, entry = signed_archive(tmp_path, version=1)
    entry.update(policy)
    registry = tmp_path / "registry.json"
    write_registry(registry, entry)
    with pytest.raises(PackageVerificationError):
        inspect_package(package, PluginPackageVerifier(load_trusted_publishers(registry)))


def test_extraction_rejects_archive_changed_after_inspection(tmp_path: Path) -> None:
    package, entry = signed_archive(tmp_path)
    registry = tmp_path / "registry.json"
    write_registry(registry, entry)
    verifier = PluginPackageVerifier(load_trusted_publishers(registry))
    verified = verifier.inspect(package)
    with zipfile.ZipFile(package, "a") as archive:
        archive.comment = b"changed between inspection and extraction"
    destination = tmp_path / "installed"
    with pytest.raises(PackageVerificationError, match="changed"):
        verifier.extract(verified, destination)
    assert not destination.exists()


def test_expiry_during_preview_cannot_become_an_overridable_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package, entry = signed_archive(tmp_path)
    entry.update(not_after=CUTOFF.isoformat(), historical_package_sha256=[])
    registry = tmp_path / "registry.json"
    write_registry(registry, entry)
    verifier = PluginPackageVerifier(load_trusted_publishers(registry))

    class Clock(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            del tz
            cls.calls += 1
            return CUTOFF - timedelta(seconds=1) if cls.calls == 1 else CUTOFF

    monkeypatch.setattr(updates, "datetime", Clock)
    with pytest.raises(PackageVerificationError, match="publisher"):
        inspect_package(package, verifier)


@pytest.mark.parametrize("version", [1, 2])
def test_even_an_explicit_archive_pin_cannot_bypass_signature_validation(
    tmp_path: Path, version: int
) -> None:
    package, entry = signed_archive(tmp_path, version=version)
    with zipfile.ZipFile(package) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members["manifest.json"])
    manifest["integrity"]["signature"] = ("v2:" if version == 2 else "") + base64.b64encode(
        b"x" * 64
    ).decode()
    members["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(package, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    entry["historical_package_sha256"] = [hashlib.sha256(package.read_bytes()).hexdigest()]
    registry = tmp_path / "registry.json"
    write_registry(registry, entry)
    verifier = PluginPackageVerifier(load_trusted_publishers(registry))
    with pytest.raises(PackageVerificationError, match="signature"):
        verifier.inspect(package)
    assert not inspect_package(package, verifier).trust.installable


@pytest.mark.parametrize("name", [".runtime-state-package.utp", ".runtime-state-package.utp/file"])
def test_archive_cannot_replace_runtime_owned_metadata(tmp_path: Path, name: str) -> None:
    package = tmp_path / "reserved.utp"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("payload/" + name, b"replace cache")
    with pytest.raises(PackageFormatError, match="reserved runtime path"):
        PluginPackageVerifier().inspect(package)
