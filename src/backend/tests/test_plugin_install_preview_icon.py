"""The consent preview never loads an address the plugin chose for its icon."""

import base64
import io
import json
import zipfile

import pytest
from test_plugin_install_sources import package_bytes

from src.api.routes import plugins
from src.plugin_api.installer import inspect_package
from src.plugin_api.updates import PluginPackageVerifier, canonical_payload_digest

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'


def preview_of(path, *, manifest_icon=None, packaged=None):
    with zipfile.ZipFile(io.BytesIO(package_bytes("example.icon"))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        files = [("plugin.py", archive.read("payload/plugin.py"))]
    files += packaged or []
    manifest["icon"] = manifest_icon
    manifest["integrity"]["sha256"] = canonical_payload_digest(files)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in files:
            archive.writestr("payload/" + name, content)
    inspected = inspect_package(path, PluginPackageVerifier(require_signature=False))
    return plugins._install_preview(inspected)


@pytest.mark.parametrize(
    "address",
    [
        "http://tracker.example/pixel.png?user=1",
        "https://tracker.example/pixel.png",
        "//tracker.example/pixel.png",
    ],
)
def test_manifest_icon_address_is_not_shown_before_consent(tmp_path, address):
    assert preview_of(tmp_path / "p.utp", manifest_icon=address)["icon"] is None


def test_packaged_icon_is_shown_as_inline_data(tmp_path):
    preview = preview_of(
        tmp_path / "p.utp",
        manifest_icon="https://tracker.example/pixel.png",
        packaged=[("icon.svg", SVG)],
    )
    assert preview["icon"] == "data:image/svg+xml;base64," + base64.b64encode(SVG).decode()
