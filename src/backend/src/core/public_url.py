"""Origin validation, independent of OIDC and notification destination trust."""

import ipaddress
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl


def normalize_public_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("App URL must be a string")
    value = value.strip()
    if not value:
        return ""
    if len(value) > 2048 or any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 or char in '<>"\\' for char in value
    ):
        raise ValueError("App URL must be an HTTP or HTTPS origin")
    parsed = urlsplit(value)
    if any(
        (
            parsed.scheme not in {"https", "http"},
            not parsed.hostname,
            len(parsed.hostname or "") > 253,
            parsed.username is not None,
            parsed.password is not None,
            parsed.path not in {"", "/"},
            bool(parsed.query),
            bool(parsed.fragment),
        )
    ):
        raise ValueError("Public app URL must contain only a scheme, host and optional port")
    return str(AnyHttpUrl(value)).rstrip("/")


def is_public_app_url(value: str) -> bool:
    """Public deployment defaults require HTTPS and a fully qualified domain name.

    DNS is deliberately not resolved here: this is link rendering, not an outbound
    request. Administrators are responsible for the configured name's public DNS.
    """
    parsed = urlsplit(normalize_public_url(value))
    hostname = parsed.hostname or ""
    if parsed.scheme != "https" or "." not in hostname:
        return False
    if hostname.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return False


def validate_deployment_url(value: str) -> str:
    normalized = normalize_public_url(value)
    if normalized and not is_public_app_url(normalized):
        raise ValueError("Shared app URL must use HTTPS and a public fully qualified domain name")
    return normalized
