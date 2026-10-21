"""OpenID Connect helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from src.core.config import settings
from src.core.oidc_http import OidcOAuth2App, OidcTransport
from src.database.models.oidc_settings import OidcSettings

oauth = OAuth()


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
        configured = config.redirect_uri.strip()
        # A provider migrated from the old single-provider configuration may
        # still contain the legacy callback path. Named providers have their
        # own callback route, so transparently upgrade that old path while
        # preserving the configured scheme/host/port.
        if config.slug != "default":
            parts = urlsplit(configured)
            if parts.path.rstrip("/") == "/api/auth/oidc/callback":
                parts = parts._replace(path=f"/api/auth/oidc/callback/{config.slug}")
                return urlunsplit(parts)
        return configured
    return (
        str(request.url_for("oidc_callback_provider", provider_slug=config.slug))
        if config.slug != "default"
        else str(request.url_for("oidc_callback"))
    )


async def validate_oidc_metadata(client: Any, config: OidcConfig) -> None:
    """Pin discovery to the configured issuer and permit only asymmetric signing."""
    metadata = await client.load_server_metadata()
    if metadata.get("issuer") != config.expected_issuer:
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


async def begin_oidc(request: Request, config: OidcConfig) -> RedirectResponse:
    client_name = f"oidc_{config.slug}" if config.slug != "default" else "oidc"
    try:
        register_oidc_provider(config, client_name)
        client = oauth.create_client(client_name)
        if client is None:
            return RedirectResponse("/login?oidc_error=provider_unavailable", 303)
        await validate_oidc_metadata(client, config)
        return await client.authorize_redirect(request, callback_url(request, config))
    # External-provider failures return a constant error without leaking URLs or secrets.
    except Exception:  # pylint: disable=broad-exception-caught
        return RedirectResponse("/login?oidc_error=authentication_failed", 303)
