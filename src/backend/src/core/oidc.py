"""OpenID Connect helpers."""

from __future__ import annotations

import logging
import socket
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from src.core.config import settings
from src.core.oidc_http import OidcOAuth2App, OidcTransport, is_loopback
from src.database.models.oidc_settings import OidcSettings
from src.helpers.public_http import parse_http_url

oauth = OAuth()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
# Provider configuration mirrors the persisted OIDC settings, including branding.
class OidcConfig:  # pylint: disable=too-many-instance-attributes
    issuer_url: str
    client_id: str
    client_secret: str
    scopes: str = "openid profile email"
    redirect_uri: str | None = None
    groups_claim: str = "groups"
    admin_group: str | None = None
    user_match_field: str = "email"
    allow_new_users: bool = True
    require_verified_email: bool = False
    discovery_url: str | None = None
    name: str = "SSO"
    slug: str = "default"
    button_text: str = "Continue with SSO"
    button_image_url: str | None = None

    @property
    def expected_issuer(self) -> str:
        # Legacy settings may contain the discovery URL instead of the issuer.
        return self.issuer_url.strip().removesuffix("/.well-known/openid-configuration")

    def accepts_issuer(self, issuer: object) -> bool:
        configured = self.issuer_url.strip()
        if configured.endswith("/.well-known/openid-configuration"):
            # Legacy discovery inputs identify this exact issuer path, with the
            # issuer's own trailing-slash convention (e.g. Authentik vs Keycloak).
            return isinstance(issuer, str) and issuer in {
                self.expected_issuer,
                self.expected_issuer + "/",
            }
        return isinstance(issuer, str) and issuer == configured

    @property
    def server_metadata_url(self) -> str:
        configured = (self.discovery_url or self.issuer_url).strip().rstrip("/")
        suffix = "/.well-known/openid-configuration"
        if configured.endswith(suffix):
            return configured
        return urljoin(configured + "/", ".well-known/openid-configuration")


_registered_configs: dict[str, OidcConfig] = {}


async def get_or_create_oidc_settings(db: AsyncSession) -> OidcSettings:
    """Load the deployment singleton without committing the caller's transaction."""
    row = await db.scalar(select(OidcSettings).limit(1))
    if row is None:
        row = OidcSettings()
        db.add(row)
        await db.flush()
    return row


def env_oidc_config() -> OidcConfig | None:
    if not (settings.OIDC_ISSUER_URL and settings.OIDC_CLIENT_ID and settings.OIDC_CLIENT_SECRET):
        return None
    issuer = settings.OIDC_ISSUER_URL.strip()
    return OidcConfig(
        issuer_url=issuer,
        client_id=settings.OIDC_CLIENT_ID,
        client_secret=settings.OIDC_CLIENT_SECRET,
        scopes=settings.OIDC_SCOPES,
        redirect_uri=settings.OIDC_REDIRECT_URI,
        groups_claim=settings.OIDC_GROUPS_CLAIM,
        admin_group=settings.OIDC_ADMIN_GROUP,
        user_match_field=getattr(settings, "OIDC_USER_MATCH_FIELD", "email"),
        require_verified_email=settings.OIDC_REQUIRE_VERIFIED_EMAIL,
        discovery_url=issuer if issuer.endswith("/.well-known/openid-configuration") else None,
    )


def register_oidc_provider(config: OidcConfig, client_name: str = "oidc") -> None:
    # Authlib's overwrite flag updates framework configuration, but it does
    # not replace an already-created client. Invalidate that client when an
    # administrator changes the issuer, credentials or scopes so sign-in uses
    # the saved configuration without requiring a server restart.
    if _registered_configs.get(client_name) != config:
        # Authlib provides no public cache invalidation method for registered clients.
        oauth._clients.pop(client_name, None)  # pylint: disable=protected-access
    oauth.register(
        name=client_name,
        client_cls=OidcOAuth2App,
        client_id=config.client_id,
        client_secret=config.client_secret,
        server_metadata_url=config.server_metadata_url,
        client_kwargs={
            "scope": config.scopes,
            "code_challenge_method": "S256",
            "transport": OidcTransport(
                config.expected_issuer,
                settings.OIDC_PRIVATE_ISSUER_ORIGINS,
                development=settings.DEBUG or settings.STARTUP_MODE in {"dev", "development"},
            ),
            "trust_env": False,
            "follow_redirects": False,
        },
        overwrite=True,
    )
    _registered_configs[client_name] = config


def callback_url(request: Request, config: OidcConfig) -> str:
    if config.redirect_uri:
        callback = config.redirect_uri.strip()
        # Preserve the named route when migrating a legacy explicit override.
        if config.slug != "default":
            parts = urlsplit(callback)
            if parts.path.rstrip("/") == "/api/auth/oidc/callback":
                callback = urlunsplit(parts._replace(path=f"/api/auth/oidc/callback/{config.slug}"))
    else:
        callback = (
            str(request.url_for("oidc_callback_provider", provider_slug=config.slug))
            if config.slug != "default"
            else str(request.url_for("oidc_callback"))
        )
    parts = parse_http_url(callback)
    if parts.scheme != "https" and not (
        (settings.DEBUG or settings.STARTUP_MODE in {"dev", "development"})
        and is_loopback(str(parts.hostname))
    ):
        raise ValueError("OIDC callback requires HTTPS")
    return callback


async def validate_oidc_metadata(client: Any, config: OidcConfig) -> str:
    """Pin discovery to the configured issuer and permit only asymmetric signing."""
    metadata = await client.load_server_metadata()
    issuer = metadata.get("issuer")
    if not config.accepts_issuer(issuer):
        raise ValueError("OIDC discovery issuer mismatch")
    transport = getattr(client, "client_kwargs", {}).get("transport")
    if isinstance(transport, OidcTransport):
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint"):
            if key in metadata:
                transport.validate_destination(metadata[key])
    permitted = {
        "RS256",
        "RS384",
        "RS512",
        "PS256",
        "PS384",
        "PS512",
        "ES256",
        "ES384",
        "ES512",
        "EdDSA",
    }
    advertised = metadata.get("id_token_signing_alg_values_supported", ["RS256"])
    if not isinstance(advertised, list):
        raise ValueError("Invalid OIDC signing algorithms")
    selected = [
        algorithm
        for algorithm in advertised
        if isinstance(algorithm, str) and algorithm in permitted
    ]
    if not selected:
        raise ValueError("No supported OIDC signing algorithm")
    metadata["id_token_signing_alg_values_supported"] = selected
    return str(issuer)


def _login_failure_reason(error: Exception) -> str:
    """Classify failures without logging URLs, credentials or provider response text."""
    cause: BaseException = error
    for _ in range(4):
        if cause.__cause__ is None:
            break
        cause = cause.__cause__
    for error_type, reason in (
        (ssl.SSLCertVerificationError, "certificate_error"),
        (ssl.SSLError, "tls_error"),
        (socket.gaierror, "dns_error"),
        (TimeoutError, "timeout"),
        (httpx.TimeoutException, "timeout"),
        (httpx.HTTPStatusError, "provider_http_error"),
        (OSError, "connection_error"),
    ):
        if isinstance(cause, error_type):
            return reason
    # Only exact, application-owned policy messages select diagnostic labels.
    # Unknown exception messages are never copied into logs.
    return {
        "OIDC callback requires HTTPS": "insecure_callback",
        "OIDC destinations require HTTPS": "insecure_destination",
        "OIDC discovery issuer mismatch": "issuer_mismatch",
        "Host must resolve exclusively to public addresses.": "private_address",
        "OIDC endpoint redirects are not allowed": "endpoint_redirect",
        "OIDC response is too large": "response_too_large",
        "Compressed OIDC responses are not supported": "compressed_response",
        "No supported OIDC signing algorithm": "unsupported_signing_algorithm",
        "Invalid OIDC signing algorithms": "invalid_signing_algorithms",
    }.get(str(cause), "provider_failure")


async def begin_oidc(request: Request, config: OidcConfig) -> RedirectResponse:
    client_name = f"oidc_{config.slug}" if config.slug != "default" else "oidc"
    stage = "registration"
    try:
        register_oidc_provider(config, client_name)
        client = oauth.create_client(client_name)
        if client is None:
            return RedirectResponse("/login?oidc_error=provider_unavailable", 303)
        stage = "callback_url"
        redirect_uri = callback_url(request, config)
        stage = "discovery"
        await validate_oidc_metadata(client, config)
        stage = "authorization_redirect"
        return await client.authorize_redirect(request, redirect_uri)
    # External-provider failures return a constant error without leaking URLs or secrets.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(
            "OIDC login failed provider=%r stage=%s reason=%s error_type=%s",
            config.slug,
            stage,
            _login_failure_reason(exc),
            type(exc).__name__,
        )
        return RedirectResponse("/login?oidc_error=authentication_failed", 303)
