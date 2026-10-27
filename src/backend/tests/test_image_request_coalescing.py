"""Collection, detail and prefetch cache misses share one actual download."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.helpers import remote_images


@pytest.mark.parametrize("failure", [False, True])
def test_concurrent_misses_share_success_or_failure_and_later_retry(monkeypatch, tmp_path, failure):
    target = tmp_path / "poster.jpg"
    entered = threading.Event()
    finish = threading.Event()
    calls = []

    def fetch(_url, path, _width):
        calls.append(1)
        entered.set()
        assert finish.wait(2)
        if failure:
            raise remote_images.RemoteImageError("unavailable")
        path.write_bytes(b"cached")

    monkeypatch.setattr(remote_images, "fetch_and_store", fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            remote_images.fetch_if_missing, "https://example.test/image", target, 400
        )
        assert entered.wait(2)
        second = pool.submit(
            remote_images.fetch_if_missing, "https://example.test/image", target, 400
        )
        time.sleep(0.03)
        finish.set()
        for result in (first, second):
            if failure:
                with pytest.raises(remote_images.RemoteImageError):
                    result.result(2)
            else:
                result.result(2)
    assert len(calls) == 1
    if failure:
        with pytest.raises(remote_images.RemoteImageError):
            remote_images.fetch_if_missing("https://example.test/image", target, 400)
        assert len(calls) == 2
