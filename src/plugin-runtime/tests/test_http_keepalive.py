"""Bodyless responses cannot corrupt the next request on a persistent socket."""

import http.client
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from runtime import RuntimeHandler, RuntimeServer


def test_delete_204_has_no_body_or_length_and_the_connection_remains_usable(monkeypatch):
    token = "x" * 32
    monkeypatch.setenv("PLUGIN_RUNTIME_TOKEN", token)
    server = RuntimeServer(("127.0.0.1", 0), RuntimeHandler)
    server.registry = SimpleNamespace(delete=Mock(), list=lambda: [])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=2)
    headers = {"X-Plugin-Runtime-Token": token}
    try:
        for plugin_id in ("example.one", "example.two"):
            connection.request("DELETE", "/plugins/" + plugin_id, headers=headers)
            response = connection.getresponse()
            assert response.status == 204
            assert response.getheader("Content-Length") is None
            assert response.read() == b""
            socket = connection.sock
            connection.request("GET", "/plugins", headers=headers)
            response = connection.getresponse()
            assert response.status == 200
            assert response.read() == b"[]"
            assert connection.sock is socket
        assert server.registry.delete.call_count == 2
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
