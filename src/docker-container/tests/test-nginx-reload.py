"""Exercise real graceful activation inside the disposable production-test container."""

import asyncio
import http.client
import ssl
import subprocess
from pathlib import Path

from src.core.nginx_configuration import (
    NginxActivationError,
    NginxConfiguration,
    apply_nginx_change,
)

ACTIVE = Path("/etc/nginx/nginx.conf")
TLS = Path("/tmp/nginx-config-tests/tls")


def request_status(secure=False):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = (
        http.client.HTTPSConnection("127.0.0.1", context=context, timeout=2)
        if secure
        else http.client.HTTPConnection("127.0.0.1", timeout=2)
    )
    try:
        connection.request("GET", "/_startup/status.json")
        return connection.getresponse().status
    finally:
        connection.close()


async def main():
    Path("/run/unnamed-tracking/status.json").write_text('{"overall":"ready"}', encoding="utf-8")
    subprocess.run(
        ["/usr/local/bin/render-production-nginx", "/etc/nginx/ready.conf", str(ACTIVE)],
        check=True,
    )
    subprocess.run(["nginx"], check=True)
    try:
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
        async with apply_nginx_change(
            NginxConfiguration(header="CF-Connecting-IP", trusted_proxies="")
        ):
            assert request_status() == 200
            assert "set_real_ip_from" not in ACTIVE.read_text(encoding="utf-8")
        assert not list(ACTIVE.parent.glob(".nginx-candidate-*"))
        print("Live Nginx HTTP/HTTPS/redirect, validation, rollback and reload tests passed")
    finally:
        subprocess.run(["nginx", "-s", "quit"], check=True)


asyncio.run(main())
