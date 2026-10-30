"""The default poster is prebuilt, while explicit low-data variants remain cached on demand."""

from PIL import Image

from src.helpers.asset_previews import ensure_preview, make_standard_previews, preview_path


def test_prebuilt_default_and_on_demand_low_data_poster(tmp_path):
    asset = tmp_path / "user" / "games" / "example" / "key_art.png"
    asset.parent.mkdir(parents=True)
    Image.new("RGB", (800, 1200)).save(asset)
    make_standard_previews(asset, "key_art", "game-id")
    cache = tmp_path / "user" / ".cache"
    default = preview_path(cache, "game-id", "key_art", 400)
    with Image.open(default) as image:
        assert image.size == (400, 600)
    before = default.stat().st_mtime_ns
    make_standard_previews(asset, "key_art", "game-id")
    assert default.stat().st_mtime_ns == before
    small = preview_path(cache, "game-id", "key_art", 240)
    assert not small.exists()
    assert ensure_preview(asset, small, 240) == (small, "image/jpeg")
    before = small.stat().st_mtime_ns
    ensure_preview(asset, small, 240)
    assert small.stat().st_mtime_ns == before
