"""Keep durable plugin manager records isolated between application tests."""

import pytest


@pytest.fixture(autouse=True)
def isolated_plugin_manager_state(monkeypatch, tmp_path):
    monkeypatch.setenv("PLUGIN_MANAGER_STATE_PATH", str(tmp_path / "plugin-manager.json"))


@pytest.fixture(autouse=True)
def isolated_game_asset_root(monkeypatch, tmp_path):
    """Keep game-folder tests out of the host's real /data filesystem."""
    from src.helpers import save_game_asset

    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path / "users")
