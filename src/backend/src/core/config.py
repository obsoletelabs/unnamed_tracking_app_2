"""Application configuration resolved through the central configuration handler."""

from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.config_registry import CONFIG_REGISTRY
from src.core.env_handler import EnvConfigHandler
from src.core.oidc_http import private_issuer_origins
from src.core.metrics_token import persistent_metrics_token
from src.core.public_url import validate_deployment_url


class Settings(BaseSettings):
    """Runtime settings; source and default policy live in EnvConfigHandler."""

    # Field names deliberately match the public environment configuration contract.
    # pylint: disable=invalid-name

    DATABASE_URL: str = ""
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: str | None = None
    POSTGRES_DB: str | None = None
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432

    STEAMGRIDDB_API_KEY: str | None = None
    RETROACHIEVEMENTS_API_KEY: str | None = None
    GIANTBOMB_API_KEY: str | None = None
    PRIMARY_USER_USERNAME: str = ""
    PRIMARY_USER_EMAIL: str = ""
    PRIMARY_USER_PASSWORD: str = ""

    AUTH_COOKIE_SECURE: bool = False
    DEBUG: bool = False
    METADATA_HEALTH_INTERVAL_SECONDS: int = 1800
    CALENDAR_SUBSCRIPTIONS_ENABLED: bool = True
    SECRET_KEY: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = Field(default=587, ge=1, le=65535)
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_ADDRESS: str = ""
    SMTP_TLS_MODE: str = "starttls"
    WEB_PUSH_VAPID_SUBJECT: str = ""
    WEB_PUSH_VAPID_PRIVATE_KEY: str = ""
    PUBLIC_APP_URL: str = ""
    DEFAULT_UI_STYLE: Literal["modern", "classic"] = "modern"
    STARTUP_MODE: str = ""
    ARGON2_MEMORY_KIB: int = Field(default=19456, ge=8192, le=262144)
    ARGON2_TIME_COST: int = Field(default=2, ge=1, le=10)
    ARGON2_PARALLELISM: int = Field(default=1, ge=1, le=8)
    MIGRATION_BACKUP_MODE: Literal["automatic", "required", "development"] = "automatic"
    MIGRATION_BACKUP_MAX_AGE_SECONDS: int = Field(default=7200, ge=60, le=7 * 86400)
    NOTIFICATION_BLOCKED_PROVIDERS: str = ""
    NOTIFICATION_BLOCKED_TYPES: str = ""
    NOTIFICATION_TIMING_ENABLED: bool = True
    NOTIFICATION_URGENT_BYPASS_ENABLED: bool = True
    NOTIFICATION_MINIMUM_TRUST: int = Field(default=0, ge=0, le=2)
    NOTIFICATION_RETENTION_DEFAULT_DAYS: int = Field(default=30, ge=0, le=3650)
    NOTIFICATION_AUDIT_RETENTION_DAYS: int = Field(default=90, ge=1, le=3650)
    NOTIFICATION_RETENTION_MAXIMUM_DAYS: int = Field(default=0, ge=0, le=3650)
    # How producer-supplied images reach rich notification pages: re-encoded and cached by this
    # server ("proxied"), loaded directly by the viewer's browser ("external") or not shown ("off").
    NOTIFICATION_IMAGE_MODE: Literal["proxied", "external", "off"] = "proxied"
    # What an external destination receives when a story it already saw changes and its provider
    # cannot edit the earlier message: "all" sends a follow-up for every revision, "important"
    # only for warning/error/critical revisions, "none" never. Edits are always allowed.
    NOTIFICATION_FOLLOW_UPS: Literal["all", "important", "none"] = "important"
    # What a browser/PWA notification may show on the device: "generic" (a constant message, the
    # default, safe for locked screens) or "story" (the item's own title, summary, one external
    # image and same-origin actions, still generic for any non-standard or non-private projection).
    NOTIFICATION_BROWSER_PREVIEWS: Literal["generic", "story"] = "generic"
    NOTIFICATION_IMAGE_CACHE_DIR: str = "/data/cache/notification-images"
    NOTIFICATION_IMAGE_MAX_BYTES: int = Field(default=5 * 1024 * 1024, ge=1024, le=15 * 1024 * 1024)
    NOTIFICATION_IMAGE_FAILURE_SECONDS: int = Field(default=300, ge=0, le=86400)
    NOTIFICATION_LIVE_LINK_DEFAULT_DAYS: int = Field(default=7, ge=1, le=365)
    NOTIFICATION_LIVE_LINK_MAXIMUM_DAYS: int = Field(default=30, ge=1, le=365)
    MAX_UPLOAD_SIZE_MB: int = 15
    MAX_DOC_SIZE_MB: int = 500
    MAX_SAVE_ARCHIVE_SIZE_MB: int = 4096
    MAX_CLIP_SIZE_MB: int = 500
    MAX_WORLD_SAVE_SIZE_MB: int = 2000
    GEOIP_DATABASE_PATH: str = "/data/GeoIP.mmdb"
    GEOIP_COUNTRY_DATABASE_PATH: str = "/data/GeoIP-Country.mmdb"
    GEOIP_ASN_DATABASE_PATH: str = "/data/GeoIP-ASN.mmdb"

    PASSWORD_MIN_LENGTH: int = 9
    PASSWORD_REQUIRE_UPPERCASE: bool = True
    PASSWORD_REQUIRE_LOWERCASE: bool = True
    PASSWORD_REQUIRE_DIGIT: bool = False
    PASSWORD_REQUIRE_SYMBOL: bool = True

    AUTH_RATE_LIMIT_PERCENT: int = Field(default=100, ge=10, le=10000)
    AUTH_LOGIN_ACCOUNT_PER_MINUTE: int = Field(default=8, ge=1, le=100000)
    AUTH_LOGIN_ACCOUNT_PER_15_MINUTES: int = Field(default=20, ge=1, le=100000)
    AUTH_LOGIN_IP_PER_MINUTE: int = Field(default=120, ge=1, le=100000)
    AUTH_LOGIN_IP_FAILURES_PER_15_MINUTES: int = Field(default=50, ge=1, le=1000000)
    AUTH_PASSWORD_WORKERS: int = Field(default=2, ge=1, le=16)

    METRICS_ENABLED: bool = False
    METRICS_TOKEN: str = ""
    # Set when the token was generated by the application rather than configured.
    METRICS_TOKEN_GENERATED: bool = False

    IGDB_CLIENT_ID: str | None = None
    IGDB_CLIENT_SECRET: str | None = None
    TMDB_API_KEY: str | None = None
    OMDB_API_KEY: str | None = None
    TVDB_API_KEY: str | None = None
    XBOX_CLIENT_ID: str | None = None
    XBOX_CLIENT_SECRET: str | None = None
    SCREENSCRAPER_DEVID: str | None = None
    SCREENSCRAPER_DEVPASSWORD: str | None = None
    SCREENSCRAPER_SSID: str | None = None
    SCREENSCRAPER_SSPASSWORD: str | None = None

    OIDC_ISSUER_URL: str | None = None
    OIDC_CLIENT_ID: str | None = None
    OIDC_CLIENT_SECRET: str | None = None
    OIDC_REDIRECT_URI: str | None = None
    OIDC_SCOPES: str = "openid profile email"
    OIDC_GROUPS_CLAIM: str = "groups"
    OIDC_ADMIN_GROUP: str | None = None
    OIDC_USER_MATCH_FIELD: str = "email"
    OIDC_REQUIRE_VERIFIED_EMAIL: bool = False
    # Legacy compatibility: the configured HTTPS issuer origin is already trusted.
    OIDC_PRIVATE_ISSUER_ORIGINS: str = ""

    # Blank values in .env (as shipped in example.env) fall back to defaults.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    @field_validator("OIDC_PRIVATE_ISSUER_ORIGINS")
    @classmethod
    def validate_oidc_private_origins(cls, value: str) -> str:
        private_issuer_origins(value)
        return value.strip()

    @field_validator("PUBLIC_APP_URL")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        return validate_deployment_url(value)

    @model_validator(mode="after")
    def require_metrics_token(self) -> "Settings":
        """/metrics is only served with a strong scrape credential; fail closed at start-up."""
        if not self.METRICS_ENABLED:
            return self
        if not self.METRICS_TOKEN:
            self.METRICS_TOKEN = persistent_metrics_token()
            self.METRICS_TOKEN_GENERATED = True
        elif len(self.METRICS_TOKEN) < 32:
            raise ValueError(
                "METRICS_TOKEN must have at least 32 characters (for example the output of "
                "`openssl rand -hex 32`); leave it empty to have one generated."
            )
        return self

    @model_validator(mode="after")
    def resolve_database(self) -> "Settings":
        components = {
            "POSTGRES_USER": self.POSTGRES_USER,
            "POSTGRES_PASSWORD": self.POSTGRES_PASSWORD,
            "POSTGRES_DB": self.POSTGRES_DB,
        }
        present = [bool(value and str(value).strip()) for value in components.values()]

        if all(present):
            self.DATABASE_URL = (
                "postgresql+psycopg://"
                f"{quote_plus(str(self.POSTGRES_USER))}:"
                f"{quote_plus(str(self.POSTGRES_PASSWORD))}@"
                f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/"
                f"{quote_plus(str(self.POSTGRES_DB))}"
            )
            return self

        # DATABASE_URL is an alternative to the component form. If both are
        # supplied, the explicit component set remains authoritative when it
        # is complete; a partial component set must not make an otherwise
        # valid DATABASE_URL deployment fail.
        if self.DATABASE_URL.strip():
            # Compatibility for existing deployments. New deployments should use
            # POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB instead.
            url = self.DATABASE_URL.strip()
            if url.startswith("postgres://"):
                url = "postgresql+psycopg://" + url.removeprefix("postgres://")
            elif url.startswith("postgresql://"):
                url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
            self.DATABASE_URL = url
            return self

        raise ValueError(
            "Database configuration is required. Set POSTGRES_USER, "
            "POSTGRES_PASSWORD, and POSTGRES_DB."
        )


logger = logging.getLogger(__name__)

_handler = EnvConfigHandler()
for _spec in CONFIG_REGISTRY:
    if _spec.deprecated and _handler.has(_spec.name):
        logger.warning("%s", _spec.deprecated_message)

_settings_values = {
    spec.name: _handler.get(spec.name)
    for spec in CONFIG_REGISTRY
    if spec.name != "VITE_USE_MOCK_DATA" and _handler.get(spec.name) is not None
}
# EnvConfigHandler owns file discovery (including ENV_FILE); a second Pydantic
# .env read would reintroduce values the handler intentionally did not load.
settings = Settings(_env_file=None, **_settings_values)  # type: ignore[call-arg]
if not settings.SECRET_KEY:
    # Preserve the environment-named field when supplying its persistent default.
    settings.SECRET_KEY = _handler.resolved()["SECRET_KEY"]  # pylint: disable=invalid-name
