"""Exercise real graceful activation inside the disposable production-test container."""

import asyncio
import http.client
import json
import os
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from src.core.nginx_configuration import (
    NginxActivationError,
    NginxConfiguration,
    apply_nginx_change,
)

ACTIVE = Path("/etc/nginx/nginx.conf")
TLS = Path("/tmp/nginx-config-tests/tls")


def request(path, secure=False, method="GET", headers=None):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = (
        http.client.HTTPSConnection("127.0.0.1", context=context, timeout=2)
        if secure
        else http.client.HTTPConnection("127.0.0.1", timeout=2)
    )
    try:
        connection.request(method, path, body=b"" if method == "POST" else None, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.getheader("location"), response.read().decode()
    finally:
        connection.close()


def request_status(secure=False):
    return request("/_startup/status.json", secure)[0]


def check_proxy_client(expected, secure=False, forwarded_proto=None):
    headers = {"X-Forwarded-For": "198.51.100.20", "X-Real-IP": "203.0.113.99"}
    if forwarded_proto is not None:
        headers["X-Forwarded-Proto"] = forwarded_proto
    code, _location, body = request(
        "/api/client",
        secure=secure,
        headers=headers,
    )
    assert code == 200
    assert json.loads(body) == {"real": expected, "forwarded": expected}, body


class ForwardedProtoEcho(BaseHTTPRequestHandler):
    """Stands in for the backend and reports the scheme Nginx forwarded."""

    def _echo(self):
        if self.path == "/api/client":
            body = json.dumps(
                {
                    "real": self.headers.get("X-Real-IP"),
                    "forwarded": self.headers.get("X-Forwarded-For"),
                }
            ).encode()
        else:
            body = (self.headers.get("X-Forwarded-Proto") or "").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _echo

    def log_message(self, *_args):
        pass


async def main():
    Path("/run/unnamed-tracking/status.json").write_text('{"overall":"ready"}', encoding="utf-8")
    subprocess.run(
        ["/usr/local/bin/render-production-nginx", "/etc/nginx/ready.conf", str(ACTIVE)],
        check=True,
    )
    subprocess.run(["nginx"], check=True)
    backend = HTTPServer(("127.0.0.1", 8000), ForwardedProtoEcho)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    try:
        # Only trusted proxies may say a request arrived over HTTPS.
        assert request("/api/echo")[2] == "http"
        assert request("/api/echo", headers={"X-Forwarded-Proto": "https"})[2] == "https"
        original = ACTIVE.read_bytes()
        configuration = NginxConfiguration(
            enabled=True,
            certificate=str(TLS / "cert.pem"),
            private_key=str(TLS / "key.pem"),
            header="X-Forwarded-For",
            trusted_proxies="127.0.0.1/32 ::1/128",
        )
        async with apply_nginx_change(configuration):
            assert request_status(secure=True) == 200
            protocol = subprocess.run(
                ["curl", "--http2", "-kfsS", "-o", "/dev/null", "-w", "%{http_version}",
                 "https://127.0.0.1/_startup/status.json"],
                check=True, capture_output=True, text=True, timeout=3,
            ).stdout
            assert protocol == "2", f"Expected HTTP/2 negotiation, got {protocol}"
            check_proxy_client("198.51.100.20")
            check_proxy_client("198.51.100.20", secure=True)
        assert ACTIVE.read_bytes() != original
        working = ACTIVE.read_bytes()
        invalid = configuration.model_copy(update={"certificate": str(TLS / "invalid.pem")})
        try:
            async with apply_nginx_change(invalid):
                raise AssertionError("Invalid certificate reached persistence")
        except NginxActivationError:
            pass
        assert ACTIVE.read_bytes() == working
        assert request_status(secure=True) == 200

        redirected = configuration.model_copy(update={"redirect_http": True})
        async with apply_nginx_change(redirected):
            assert request_status() == 301
            assert request_status(secure=True) == 200
            assert request("/api/echo", secure=True)[2] == "https"
            check_proxy_client("198.51.100.20", secure=True)
            check_proxy_client("198.51.100.20", forwarded_proto="https")
            assert request("/library")[:2] == (301, "https://127.0.0.1/library")
            # A trusted HTTPS proxy's requests are not redirected again.
            status, _location, scheme = request(
                "/api/echo", headers={"X-Forwarded-Proto": "https"}
            )
            assert (status, scheme) == (200, "https")
            # The plugin runtime reaches the gateway over internal HTTP.
            assert request("/api/plugins/runtime/gateway", method="POST")[0] == 200
            subprocess.run(
                ["curl", "-kfsSL", "http://127.0.0.1/_startup/status.json"],
                check=True,
                capture_output=True,
            )
        working = ACTIVE.read_bytes()
        try:
            async with apply_nginx_change(configuration):
                assert request_status() == 200
                raise RuntimeError("Simulated database commit failure")
        except RuntimeError:
            pass
        assert ACTIVE.read_bytes() == working
        assert request_status() == 301
        assert request_status(secure=True) == 200

        async with apply_nginx_change(redirected):
            pass  # Reload unchanged settings after certificate renewal.
        # The redirect names the HTTPS port clients reach when it is not 443.
        os.environ["NGINX_TLS_REDIRECT_PORT"] = "8443"
        try:
            async with apply_nginx_change(redirected):
                assert request("/library")[:2] == (301, "https://127.0.0.1:8443/library")
                assert request_status(secure=True) == 200
        finally:
            del os.environ["NGINX_TLS_REDIRECT_PORT"]
        async with apply_nginx_change(
            NginxConfiguration(header="CF-Connecting-IP", trusted_proxies="")
        ):
            assert request_status() == 200
            assert "set_real_ip_from" not in ACTIVE.read_text(encoding="utf-8")
            # Without trusted proxies a client cannot claim HTTPS.
            assert request("/api/echo", headers={"X-Forwarded-Proto": "https"})[2] == "http"
            check_proxy_client("127.0.0.1")
        assert not list(ACTIVE.parent.glob(".nginx-candidate-*"))
        print("Live Nginx HTTP/HTTPS/redirect, validation, rollback and reload tests passed")
    finally:
        backend.shutdown()
        subprocess.run(["nginx", "-s", "quit"], check=True)


asyncio.run(main())
