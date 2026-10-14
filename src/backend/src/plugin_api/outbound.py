"""Bounded JSON HTTP for network-granted plugins running in isolated workers."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
import ssl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPSConnection
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 4 * 1024 * 1024
# Provider bearer tokens may contain several KiB of signed claims.
MAX_AUTHORIZATION_LENGTH = 16 * 1024


def _public_connection(normalized: str) -> HTTPSConnection:
    """Pin public DNS results while retaining the hostname for TLS/SNI validation."""
    parts = urlsplit(normalized)
    hostname = str(parts.hostname)
    port = parts.port or 443
    addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Instance hostname has no addresses.")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError("Instance must resolve exclusively to public addresses.")
    connection = HTTPSConnection(hostname, port, timeout=8, context=ssl.create_default_context())
    selected = addresses[0]

    def pinned_connection(*_args: Any, **_kwargs: Any) -> socket.socket:
        sock = socket.socket(selected[0], selected[1], selected[2])
        sock.settimeout(8)
        try:
            sock.connect(selected[4])
            return sock
        except OSError:
            sock.close()
            raise

    # HTTPSConnection.connect uses this factory and retains hostname/SNI checks.
    connection._create_connection = pinned_connection  # type: ignore[attr-defined]  # pylint: disable=protected-access
    return connection


def public_instance_request(
    origin: str,
    path: str,
    *,
    method: str = "GET",
    credential: str | None = None,
    body: dict[str, Any] | None = None,
    binary: bool = False,
) -> dict[str, Any]:
    """Bounded delegated HTTPS; no redirects, ambient proxy or DNS rebinding."""
    from src.api.schemas.delegation import integration_origin  # pylint: disable=import-outside-toplevel

    if method not in {"GET", "POST", "PATCH", "DELETE"} or not path.startswith(
        "/api/delegation/v1/"
    ):
        raise ValueError("Unsupported delegated instance operation.")
    if any(char in path for char in ("\\", "\r", "\n", "#")) or ".." in path.split("/"):
        raise ValueError("Invalid delegated instance path.")
    headers = {"Accept": "image/png" if binary else "application/json"}
    if credential:
        if not credential.startswith("utd_") or len(credential) > 256:
            raise ValueError("Invalid delegated credential.")
        headers["Authorization"] = "Bearer " + credential
    data = json.dumps(body, allow_nan=False).encode() if body is not None else None
    if data is not None:
        if len(data) > 48 * 1024:
            raise ValueError("Delegated request is too large.")
        headers["Content-Type"] = "application/json"
    return _instance_response(
        integration_origin(origin), path, method, headers=headers, data=data, binary=binary
    )


def legacy_instance_request(origin: str, path: str, *, credential: str) -> dict[str, Any]:
    """Explicit legacy compatibility permits only identity and bounded own-library reads."""
    from src.api.schemas.delegation import integration_origin  # pylint: disable=import-outside-toplevel

    if not re.fullmatch(r"utk_[A-Za-z0-9_-]{20,252}", credential):
        raise ValueError("Enter an Unnamed Tracking API key.")
    if path != "/api/auth/me" and not re.fullmatch(
        r"/api/game/list\?skip=[0-9]{1,4}&limit=200", path
    ):
        raise ValueError("Unsupported legacy instance operation.")
    return _instance_response(
        integration_origin(origin),
        path,
        "GET",
        headers={"Accept": "application/json", "Authorization": "Bearer " + credential},
        data=None,
        binary=False,
    )


def public_image_request(url: str) -> dict[str, Any]:
    """Read a bounded public raster image without credentials or redirect forwarding."""
    parts = urlsplit(_validated_url(url))
    if parts.scheme != "https":
        raise ValueError("Public images require HTTPS.")
    connection = _public_connection("https://" + parts.netloc)
    try:
        connection.request(
            "GET",
            parts.path + ("?" + parts.query if parts.query else ""),
            headers={"Accept": "image/png,image/jpeg,image/webp"},
        )
        response = connection.getresponse()
        content = response.read(2 * 1024 * 1024 + 1)
        if response.status != 200 or len(content) > 2 * 1024 * 1024:
            raise ValueError("Public image unavailable.")
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            media_type = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            media_type = "image/jpeg"
        elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            media_type = "image/webp"
        else:
            raise ValueError("Unsupported public image format.")
        return {"content": content, "media_type": media_type}
    finally:
        connection.close()


def _instance_response(
    normalized: str,
    path: str,
    method: str,
    *,
    headers: dict[str, str],
    data: bytes | None,
    binary: bool,
) -> dict[str, Any]:
    """One pinned TLS transport for both deliberately separate authorization protocols."""
    connection = _public_connection(normalized)
    try:
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("Instance response exceeded its size limit.")
        if not 200 <= response.status < 300:
            return {"status": response.status}
        if binary and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Instance artwork is not PNG.")
        if binary:
            return {"status": response.status, "content": content}
        if response.status == 204:
            return {"status": 204, "data": {}}
        result = json.loads(content)
        if not isinstance(result, (dict, list)):
            raise ValueError("Instance returned an invalid response.")
        return {"status": response.status, "data": result}
    finally:
        connection.close()


class NoRedirects(HTTPRedirectHandler):
    """Do not forward plugin credentials to a redirect destination."""

    # urllib calls this override with its fixed positional callback signature.
    # pylint: disable-next=too-many-positional-arguments
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirect refused; configure the final server URL.")


def _validated_url(url: object) -> str:
    """Allow HTTP(S) endpoints without URL credentials, fragments or ambiguous separators."""
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError("Invalid outbound URL.")
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError as exc:
        raise ValueError("Invalid outbound URL.") from exc
    if any(
        (
            parts.scheme not in {"https", "http"},
            not parts.hostname,
            parts.username,
            parts.password,
            parts.fragment,
            any(c.isspace() for c in url),
            "\\" in url,
        )
    ):
        raise ValueError("Use an HTTP(S) URL without credentials or fragment.")
    return url


def _valid_header(key: object, value: object) -> bool:
    if not isinstance(key, str) or not isinstance(value, str):
        return False
    return (
        len(key) <= 128
        and len(value) <= (MAX_AUTHORIZATION_LENGTH if key.lower() == "authorization" else 2048)
        and "\n" not in key + value
        and "\r" not in key + value
        and key.lower()
        in {
            "accept",
            "authorization",
            "x-emby-token",
            "client-id",
            "content-type",
            "user-agent",
            "referer",
            "origin",
            "cookie",
        }
    )


def _validated_headers(headers: object) -> dict[str, str]:
    if (
        not isinstance(headers, dict)
        or len(headers) > 16
        or any(not _valid_header(key, value) for key, value in headers.items())
    ):
        raise ValueError("Invalid outbound headers.")
    return headers


def _outbound_request(payload: dict[str, Any]) -> Request:
    """Validate the request before the isolated worker performs any network operation."""
    url = _validated_url(payload.get("url"))
    headers = _validated_headers(payload.get("headers", {}))
    method = payload.get("method", "GET")
    if method not in {"GET", "POST"}:
        raise ValueError("Outbound method must be GET or POST.")
    body_data = None
    if "body" in payload:
        if method != "POST" or not isinstance(payload["body"], dict):
            raise ValueError("Only POST supports an object JSON body.")
        try:
            body_data = json.dumps(payload["body"], allow_nan=False).encode()
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid outbound JSON body.") from exc
        if len(body_data) > 64 * 1024:
            raise ValueError("Outbound request exceeded the 64 KiB limit.")
    if "text_body" in payload:
        if "body" in payload or method != "POST" or not isinstance(payload["text_body"], str):
            raise ValueError("Only POST supports an exclusive text body.")
        body_data = payload["text_body"].encode("utf-8")
        if len(body_data) > 64 * 1024:
            raise ValueError("Outbound request exceeded the 64 KiB limit.")
    return Request(
        url,
        data=body_data,
        method=method,
        headers={
            "Accept": "application/json",
            **headers,
            **({"Content-Type": "application/json"} if "body" in payload else {}),
        },
    )


def outbound_json(payload: dict[str, Any]) -> dict[str, Any]:
    """Bounded JSON/text GET/POST; never return remote error bodies or credentials."""
    response_format = payload.get("response_format", "json")
    if response_format not in {"json", "text"}:
        raise ValueError("Unsupported outbound response format.")
    request = _outbound_request(payload)
    try:
        with build_opener(NoRedirects()).open(request, timeout=8) as response:
            body = response.read(MAX_BYTES + 1)
            status = response.status
    except HTTPError as exc:
        code = exc.code
        retry = exc.headers.get("Retry-After", "")
        exc.close()
        result = {"status": code, "error": "Remote server rejected the request."}
        if retry.isdecimal():
            result["retry_after_seconds"] = min(int(retry), 3600)
        elif retry:
            try:
                delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                result["retry_after_seconds"] = min(3600, max(0, math.ceil(delay)))
            except (ValueError, TypeError, OverflowError):
                pass
        return result
    except (URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return {
                "status": 0,
                "code": "certificate_untrusted",
                "error": "The remote TLS certificate is not trusted.",
            }
        return {
            "status": 0,
            "code": "unreachable",
            "error": "Cannot reach the remote server; check TLS and egress.",
        }
    if len(body) > MAX_BYTES:
        raise ValueError("Outbound response exceeded the 4 MiB limit.")
    if response_format == "text":
        try:
            return {"status": status, "text": body.decode("utf-8")}
        except UnicodeDecodeError as exc:
            raise ValueError("Remote server returned invalid text.") from exc
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Remote server returned invalid JSON.") from exc
    if not isinstance(data, (dict, list, bool)):
        raise ValueError("Remote server returned an unexpected JSON shape.")
    return {"status": status, "data": data}
