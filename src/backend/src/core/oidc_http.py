"""OIDC-only outbound policy; public requests reuse the shared DNS-pinned connection."""

from __future__ import annotations

import asyncio
import ipaddress
import ssl
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import SplitResult, urlunsplit

import httpx
from authlib.integrations.starlette_client import StarletteOAuth2App

from src.helpers.public_http import parse_http_url, public_connection

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024


def origin(parts: SplitResult) -> tuple[str, str, int]:
    return (
        parts.scheme,
        str(parts.hostname).lower(),
        parts.port or (443 if parts.scheme == "https" else 80),
    )


def private_issuer_origins(value: str) -> set[tuple[str, str, int]]:
    """Validate the legacy optional origin setting without broadening issuer trust."""
    result = set()
    for entry in value.split(","):
        if not entry.strip():
            continue
        parts = parse_http_url(entry.strip())
        if parts.scheme != "https" or parts.path not in {"", "/"} or parts.query:
            raise ValueError("OIDC private issuer exceptions must be HTTPS origins")
        result.add(origin(parts))
    return result


def is_loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class OidcTransport(httpx.AsyncBaseTransport):
    """Bound responses, refuse redirects and keep every connection within issuer policy."""

    def __init__(self, issuer: str, exceptions: str = "", *, development: bool = False):
        parts = parse_http_url(issuer)
        if parts.query:
            raise ValueError("OIDC issuer must not have a query")
        self.issuer_origin = origin(parts)
        # Preserve validation of legacy configuration, but the administrator-selected
        # HTTPS issuer already defines the exact origin trusted for private routing.
        private_issuer_origins(exceptions)
        self.private_origin = self.issuer_origin if parts.scheme == "https" else None
        self.development = development
        self.validate_destination(issuer)

    def validate_destination(self, url: str) -> SplitResult:
        parts = parse_http_url(url)
        if parts.scheme != "https" and not (self.development and is_loopback(str(parts.hostname))):
            raise ValueError("OIDC destinations require HTTPS")
        return parts

    def _connection(self, url: str) -> HTTPConnection:
        parts = self.validate_destination(url)
        if origin(parts) == self.private_origin:
            # DNS may route a trusted issuer internally. TLS still verifies its
            # hostname and CA; discovery cannot grant this trust to another origin.
            return HTTPSConnection(
                str(parts.hostname),
                parts.port or 443,
                timeout=8,
                context=ssl.create_default_context(),
            )
        # A literal loopback address avoids a second DNS lookup for localhost.
        if self.development and is_loopback(str(parts.hostname)):
            host = "127.0.0.1" if parts.hostname == "localhost" else str(parts.hostname)
            connection_cls = HTTPSConnection if parts.scheme == "https" else HTTPConnection
            return connection_cls(
                host, parts.port or (443 if parts.scheme == "https" else 80), timeout=8
            )
        return public_connection(url, timeout=8, connect_timeout=5)

    def _send(self, request: httpx.Request, body: bytes) -> httpx.Response:
        parts = self.validate_destination(str(request.url))
        connection = self._connection(str(request.url))
        try:
            headers = dict(request.headers)
            headers["accept-encoding"] = "identity"
            target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            connection.request(request.method, target, body=body, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ValueError("OIDC endpoint redirects are not allowed")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Compressed OIDC responses are not supported")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise ValueError("OIDC response is too large")
            return httpx.Response(response.status, headers=response.getheaders(), content=payload)
        finally:
            connection.close()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        if len(body) > MAX_REQUEST_BYTES:
            raise httpx.TransportError("OIDC request is too large")
        try:
            return await asyncio.to_thread(self._send, request, body)
        except (OSError, ValueError) as exc:
            # URLs, token responses and authorization headers can contain secrets.
            raise httpx.TransportError("OIDC provider connection failed") from exc


class OidcOAuth2App(StarletteOAuth2App):
    """Discovery is protocol data, never HTTP-client configuration."""

    def _get_oauth_client(self, **metadata):
        # Authlib merges discovery into client kwargs. Do not let a provider inject
        # proxy/mount/transport/TLS settings, headers or PKCE overrides.
        allowed = {
            "authorization_endpoint",
            "token_endpoint",
            "token_endpoint_auth_methods_supported",
            "response_types_supported",
            "code_challenge_methods_supported",
        }
        return super()._get_oauth_client(
            **{key: value for key, value in metadata.items() if key in allowed}
        )
