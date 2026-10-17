"""Signed-package conformance for the versioned notification contract (throwaway keys only)."""

import base64
import json
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.plugin_api.capabilities import capability_definition
from src.plugin_api.installer import inspect_package
from src.plugin_api.updates import (
    PackageFormatError,
    PackageVerificationError,
    PluginPackageVerifier,
    TrustedPublisher,
    canonical_payload_digest,
)

CONTRACT_CAPABILITIES = (
    "notifications.canonical",
    "notifications.actions",
    "notifications.urgency",
    "notifications.templates.html",
)


def refs(names):
    return [{"name": name, "version": 1} for name in names]


def permissions(names):
    return [
        {"capability": {"name": name, "version": 1}, "rationale": f"Needed for {name}."}
        for name in names
    ]


def build(tmp_path, *, api="1.1.14", names=CONTRACT_CAPABILITIES, key=None):
    """A v2-signed package whose signature envelope binds its manifest, with a throwaway key."""
    key = key or Ed25519PrivateKey.generate()
    manifest = {
        "api_contract_version": api,
        "plugin_id": "official.notifier",
        "name": "Notifier",
        "version": "1.0.0",
        "entrypoint": "plugin:main",
        "sdk_version_range": "*",
        "application_version_range": "*",
        "capabilities": refs(names),
        "permissions": permissions(names),
    }
    payload = {
        "plugin.py": b"def main():\n    return None\n",
        "package-signature-v2.json": json.dumps(
            {"schema_version": 2, "key_id": "throwaway", "manifest": manifest}
        ).encode(),
    }
    digest = canonical_payload_digest(list(payload.items()))
    signature = base64.b64encode(key.sign(f"plugin-package-v2:{digest}".encode())).decode()
    manifest = {
        **manifest,
        "integrity": {"sha256": digest, "key_id": "throwaway", "signature": "v2:" + signature},
    }
    path = tmp_path / "notifier.utp"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in payload.items():
            archive.writestr("payload/" + name, data)
    verifier = PluginPackageVerifier(
        {
            "throwaway": TrustedPublisher(
                "throwaway",
                key.public_key().public_bytes_raw(),
                channel="official",
                plugin_id_prefixes=("official.",),
            )
        }
    )
    return path, verifier, key


def test_a_signed_package_declares_the_notification_contract_capabilities(tmp_path):
    path, verifier, _ = build(tmp_path)
    inspected = inspect_package(path, verifier)
    assert inspected.trust.signature_verified and inspected.trust.installable
    manifest = inspected.package.manifest
    assert manifest.api_contract_version == "1.1.14"
    assert {item.name.value for item in manifest.capabilities} == set(CONTRACT_CAPABILITIES)
    assert {item.capability.name.value for item in manifest.permissions} == set(
        CONTRACT_CAPABILITIES
    )


@pytest.mark.parametrize("name", CONTRACT_CAPABILITIES)
def test_older_contract_versions_cannot_declare_the_new_capabilities(tmp_path, name):
    # Correctly signed, but a 1.1.13 manifest does not know these capabilities.
    path, verifier, _ = build(tmp_path, api="1.1.13", names=(name,))
    with pytest.raises(PackageFormatError, match="manifest is invalid"):
        inspect_package(path, verifier)


def test_older_manifests_without_the_new_capabilities_remain_installable(tmp_path):
    path, verifier, _ = build(tmp_path, api="1.1.13", names=("notifications.emit",))
    inspected = inspect_package(path, verifier)
    assert (
        inspected.trust.installable and inspected.package.manifest.api_contract_version == "1.1.13"
    )


def test_adding_a_capability_after_signing_breaks_the_signature_envelope(tmp_path):
    path, verifier, _ = build(tmp_path, names=("notifications.canonical",))
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    manifest["capabilities"] += refs(["notifications.templates.html"])
    manifest["permissions"] += permissions(["notifications.templates.html"])
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(PackageVerificationError, match="envelope"):
        inspect_package(path, verifier)


def test_a_package_signed_by_another_key_is_blocked(tmp_path):
    path, verifier, _ = build(tmp_path)
    verifier.publishers["throwaway"] = TrustedPublisher(
        "throwaway", Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    )
    inspected = inspect_package(path, verifier)
    assert inspected.trust.status == "invalid_signature" and not inspected.trust.installable


def test_capability_review_metadata_marks_the_authority_each_grant_carries():
    risks = {name: capability_definition(name) for name in CONTRACT_CAPABILITIES}
    assert risks["notifications.templates.html"].highly_privileged
    for name in ("notifications.canonical", "notifications.actions", "notifications.urgency"):
        assert risks[name].risk.value == "high" and not risks[name].highly_privileged
        assert risks[name].category == "Notifications"
