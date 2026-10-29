"""Public HTTP connections shared by host resources and delegated instance transport."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import time
from collections.abc import Collection
from http.client import HTTPConnection, HTTPSConnection
from typing import cast
from urllib.parse import SplitResult, urlsplit

AddressInfo = tuple[int, int, int, str, tuple[str, int] | tuple[str, int, int, int]]


def parse_http_url(url: object, *, allowed_ports: Collection[int] | None = None) -> SplitResult:
    """Validate URL syntax without imposing a caller's network permission policy."""
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError("Invalid outbound URL.")
    try:
        parts = urlsplit(url)
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Invalid outbound URL.") from exc
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("Use an HTTP(S) URL with a hostname.")
    if (
        parts.username is not None
        or parts.password is not None
        or "#" in url
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
        or "\\" in url
    ):
        raise ValueError("Use an HTTP(S) URL without credentials or fragment.")
    if allowed_ports is not None and port not in allowed_ports:
        raise ValueError("Public image URLs may only use ports 80 and 443.")
    return parts


_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".home.arpa", ".corp")


def names_a_private_host(hostname: str) -> bool:
    """True for an address literal that is not public, `localhost` and single-label or
    local-looking names.

    A syntax-only check for places that hand a URL to the browser instead of fetching it (an
    image fallback redirect): the host never resolves the name there, so DNS is not consulted,
    but a plugin must not be able to aim the viewer's browser at their own LAN by address."""
    name = hostname.strip().rstrip(".").lower().strip("[]")
    if not name or name == "localhost" or "." not in name and ":" not in name:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return name.endswith(_LOCAL_SUFFIXES)
    return not ip.is_global or ip.is_multicast


def public_addresses(hostname: str, port: int) -> list[AddressInfo]:
    """Resolve once and reject the entire answer if any address is not public."""
    addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Host has no addresses.")
    for address in addresses:
        if address[0] not in {socket.AF_INET, socket.AF_INET6}:
            raise ValueError("Host returned an unsupported address family.")
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError("Host must resolve exclusively to public addresses.")
    # IPv4/IPv6 families guarantee their corresponding socket-address tuple shape.
    return [cast(AddressInfo, address) for address in addresses]


def public_connection(
    url: str, *, timeout: float = 8, connect_timeout: float | None = None
) -> HTTPConnection:
    """Pin approved addresses and retain the original hostname for Host, TLS and SNI."""
    parts = parse_http_url(url)
    hostname = str(parts.hostname)
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    addresses = public_addresses(hostname, port)
    connection = (
        HTTPSConnection(hostname, port, timeout=timeout, context=ssl.create_default_context())
        if parts.scheme == "https"
        else HTTPConnection(hostname, port, timeout=timeout)
    )

    def pinned_connection(*_args: object, **_kwargs: object) -> socket.socket:
        deadline = time.monotonic() + (connect_timeout or timeout)
        last_error: OSError = OSError("Public host could not be reached.")
        for family, kind, protocol, _, address in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Public connection timed out.")
            sock = socket.socket(family, kind, protocol)
            sock.settimeout(remaining)
            try:
                sock.connect(address)
                sock.settimeout(timeout)
                return sock
            except OSError as exc:
                last_error = exc
                sock.close()
        raise last_error

    # http.client exposes no public factory hook; its connect method keeps hostname/TLS checks.
    connection._create_connection = pinned_connection  # type: ignore[attr-defined]  # pylint: disable=protected-access
    return connection
