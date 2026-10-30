import threading

from runtime import deprecated_image_notice, start_deprecation_notices


def test_current_image_name_logs_nothing():
    assert deprecated_image_notice({}) is None
    assert deprecated_image_notice({"PLUGIN_RUNTIME_REPLACEMENT_IMAGE": "  "}) is None


def test_deprecated_image_names_its_replacement():
    notice = deprecated_image_notice(
        {"PLUGIN_RUNTIME_REPLACEMENT_IMAGE": "ghcr.io/example/unnamed_tracking_plugin_runtime"}
    )
    assert notice is not None
    assert "ghcr.io/example/unnamed_tracking_plugin_runtime" in notice


def test_notice_is_logged_at_startup_and_repeated(capsys):
    seen = threading.Event()
    stop = start_deprecation_notices("move to the new image", interval=0.01)
    try:
        for _ in range(200):
            if capsys.readouterr().err.count("move to the new image") >= 1:
                seen.set()
                break
            threading.Event().wait(0.01)
        assert seen.is_set()
        threading.Event().wait(0.1)
        assert capsys.readouterr().err.count("move to the new image") >= 2
    finally:
        stop.set()
