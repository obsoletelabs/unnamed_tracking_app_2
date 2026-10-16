"""Acceptance acquisition stays attached to the host's current download boundary.

This is a repository-level test of ``tools/check_plugin_repository_lifecycle.py``.
It needs the complete checkout rather than the backend-only ``/app`` container
layout, so it lives beside the tool and runs from the repository root with
``src/backend`` importable. See the development wiki for the commands.
"""

import importlib
from pathlib import Path

import pytest

from src.api.routes.plugin_manager import acquisition

# The acceptance runner is the sibling ``tools`` directory of this test package.
TOOLS = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_acceptance_substitutes_owned_files_and_delegates_remote_downloads(
    tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(TOOLS))
    acceptance = importlib.import_module("check_plugin_repository_lifecycle")
    assert Path(acceptance.__file__).resolve() == TOOLS / "check_plugin_repository_lifecycle.py"
    remote_calls = []

    async def remote_download(url, *, json_document=False):
        remote_calls.append((url, json_document))
        return tmp_path / "remote", "remote.json", 12

    monkeypatch.setattr(acquisition, "download_remote_file", remote_download)
    source = tmp_path / "release-source"
    source.mkdir()
    (source / "list.json").write_bytes(b'{"plugins": []}')
    acceptance.configure_downloads(tmp_path)

    path, name, size = await acquisition.download_remote_file(
        acceptance.FIXTURE_BASE + "/list.json", json_document=True
    )
    try:
        assert path.read_bytes() == b'{"plugins": []}'
        assert name == "list.json" and size == 15
        assert remote_calls == []
    finally:
        path.unlink()

    result = await acquisition.download_remote_file(
        "https://example.com/catalogue.json", json_document=True
    )
    assert result == (tmp_path / "remote", "remote.json", 12)
    assert remote_calls == [("https://example.com/catalogue.json", True)]
