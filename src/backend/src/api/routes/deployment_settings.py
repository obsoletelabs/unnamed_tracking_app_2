"""Administrator settings with environment locks and encrypted provider secrets."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import partial
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.app_integrations import get_or_create_app_integration_settings, get_public_app_url
from src.core.auth import get_current_admin
from src.core.crypto import encrypt_secret
from src.core.env_handler import EnvConfigHandler
from src.core.nginx_configuration import (
    NGINX_TLS_FIELDS,
    NginxActivationError,
    apply_nginx_change,
    effective_nginx_configuration,
    lock_nginx_configuration,
    nginx_runtime_available,
    validate_nginx_value,
)
from src.core.oidc import get_or_create_oidc_settings
from src.core.provider_credentials import apply_deployment_provider_credentials
from src.core.public_url import validate_deployment_url
from src.core.real_ip import (
    get_effective_real_ip_config,
    validate_real_ip_header,
    validate_trusted_proxies,
)
from src.database.models.app_integration_settings import AppIntegrationSettings
from src.database.models.oidc_settings import OidcSettings
from src.database.models.user import User
from src.database.session import get_db
from src.features.notification_push_config import (
    PUSH_FIELDS,
    push_configuration,
    validate_push_value,
)
from src.features.smtp_configuration import SMTP_FIELDS, smtp_configuration, validate_smtp_value

_DB_DEFAULT = Depends(get_db)
_ADMIN_DEFAULT = Depends(get_current_admin)

router = APIRouter(
    prefix="/api/settings/deployment", tags=["settings"], dependencies=[Depends(get_current_admin)]
)


class DeploymentSettingsRequest(BaseModel):
    nginx_tls_enabled: bool | None = None
    nginx_tls_redirect_http: bool | None = None
    nginx_tls_certificate: str | None = Field(default=None, max_length=512)
    nginx_tls_private_key: str | None = Field(default=None, max_length=512)
    reload_nginx: bool = False
    public_app_url: str | None = Field(default=None, max_length=2048)
    smtp_host: str | None = Field(default=None, max_length=253)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=254)
    smtp_password: str | None = Field(default=None, max_length=4096)
    smtp_from_address: str | None = Field(default=None, max_length=254)
    smtp_tls_mode: Literal["starttls", "ssl", "none"] | None = None
    web_push_vapid_subject: str | None = Field(default=None, max_length=1024)
    web_push_vapid_private_key: str | None = Field(default=None, max_length=2048)
    steamgriddb_api_key: str | None = None
    retroachievements_api_key: str | None = None
    giantbomb_api_key: str | None = None
    igdb_client_id: str | None = None
    igdb_client_secret: str | None = None
    screenscraper_ssid: str | None = None
    screenscraper_sspassword: str | None = None
    screenscraper_devid: str | None = None
    screenscraper_devpassword: str | None = None
    xbox_client_id: str | None = None
    xbox_client_secret: str | None = None
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_scopes: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_groups_claim: str | None = None
    oidc_admin_group: str | None = None
    oidc_user_match_field: str | None = None
    oidc_enabled: bool | None = None
    oidc_default_login_method: str | None = None
    oidc_login_button_text: str | None = None
    oidc_allow_new_users: bool | None = None
    oidc_providers_json: str | None = None
    nginx_realip_header: str | None = None
    nginx_realip_trusted_proxies: str | None = None


# Independent settings endpoints expose the same provider secret-field contract.
# pylint: disable=duplicate-code
_SECRET_FIELDS = {
    "smtp_password",
    "web_push_vapid_private_key",
    "steamgriddb_api_key",
    "retroachievements_api_key",
    "giantbomb_api_key",
    "igdb_client_secret",
    "screenscraper_sspassword",
    "screenscraper_devpassword",
    "xbox_client_secret",
}
# pylint: enable=duplicate-code
_SAFE_PROVIDER_FIELDS = {
    "public_app_url",
    *(set(SMTP_FIELDS.values()) - {"smtp_password"}),
    "web_push_vapid_subject",
    "igdb_client_id",
    "screenscraper_ssid",
    "screenscraper_devid",
    "xbox_client_id",
}

_PROVIDER_ENV_NAMES = {
    "public_app_url": "PUBLIC_APP_URL",
    **{attribute: name for name, attribute in SMTP_FIELDS.items()},
    **{attribute: name for name, attribute in PUSH_FIELDS.items()},
    "steamgriddb_api_key": "STEAMGRIDDB_API_KEY",
    "retroachievements_api_key": "RETROACHIEVEMENTS_API_KEY",
    "giantbomb_api_key": "GIANTBOMB_API_KEY",
    "igdb_client_id": "IGDB_CLIENT_ID",
    "igdb_client_secret": "IGDB_CLIENT_SECRET",
    "screenscraper_ssid": "SCREENSCRAPER_SSID",
    "screenscraper_sspassword": "SCREENSCRAPER_SSPASSWORD",
    "screenscraper_devid": "SCREENSCRAPER_DEVID",
    "screenscraper_devpassword": "SCREENSCRAPER_DEVPASSWORD",
    "xbox_client_id": "XBOX_CLIENT_ID",
    "xbox_client_secret": "XBOX_CLIENT_SECRET",
}

_OIDC_ENV_NAMES = {
    "issuer_url": "OIDC_ISSUER_URL",
    "client_id": "OIDC_CLIENT_ID",
    "client_secret": "OIDC_CLIENT_SECRET",
    "scopes": "OIDC_SCOPES",
    "redirect_uri": "OIDC_REDIRECT_URI",
    "groups_claim": "OIDC_GROUPS_CLAIM",
    "admin_group": "OIDC_ADMIN_GROUP",
    "user_match_field": "OIDC_USER_MATCH_FIELD",
}

_OIDC_ENV_LOCKED_FIELDS = {
    "oidc_issuer_url",
    "oidc_client_id",
    "oidc_client_secret",
    "oidc_scopes",
    "oidc_redirect_uri",
    "oidc_groups_claim",
    "oidc_admin_group",
    "oidc_user_match_field",
    "oidc_allow_new_users",
    "oidc_enabled",
}


def _provider_view(raw):
    item = dict(raw)
    secret = item.pop("client_secret", "")
    item["client_secret_configured"] = bool(secret)
    return item


def _validate_provider_value(field: str, value):
    try:
        if field == "public_app_url":
            return validate_deployment_url(value or "")
        if field in SMTP_FIELDS.values():
            return validate_smtp_value(field, value)
        if field in PUSH_FIELDS.values():
            return validate_push_value(field, value)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid {field} configuration") from exc
    return value


def _provider_rows(row):
    try:
        data = json.loads(row.providers_json or "[]")
    except (TypeError, ValueError):
        data = []
    return [p for p in data if isinstance(p, dict) and p.get("enabled", True) and p.get("slug")]


async def get_deployment_settings(db: AsyncSession, admin: User) -> dict:
    app = await get_or_create_app_integration_settings(db)
    oidc = await get_or_create_oidc_settings(db)
    handler = EnvConfigHandler()
    provider_locks = {
        field: handler.has(env_name) for field, env_name in _PROVIDER_ENV_NAMES.items()
    }
    providers = {
        field: None if provider_locks[field] else getattr(app, field)
        for field in _SAFE_PROVIDER_FIELDS
    }
    for field in _SECRET_FIELDS:
        providers[field + "_configured"] = bool(getattr(app, field)) or provider_locks[field]
    oidc_locks = {field: handler.has(env_name) for field, env_name in _OIDC_ENV_NAMES.items()}
    named = [_provider_view(p) for p in _provider_rows(oidc)]
    real_ip = get_effective_real_ip_config(
        handler,
        app.nginx_realip_header,
        app.nginx_realip_trusted_proxies,
    )
    smtp = await smtp_configuration(db)
    push = await push_configuration(db)
    return {
        "nginx": {
            **effective_nginx_configuration(app, handler).model_dump(
                exclude={"header", "trusted_proxies"}
            ),
            "runtime_available": nginx_runtime_available(),
            "locked": {
                attribute.removeprefix("nginx_tls_"): handler.has(name)
                for name, attribute in NGINX_TLS_FIELDS.items()
            },
        },
        "app": {
            "public_app_url": await get_public_app_url(db) or None,
            "last_app_url": admin.last_app_url,
            "url_locked": handler.has("PUBLIC_APP_URL"),
        },
        "smtp": {
            "configured": smtp.configured,
            "tls_mode": smtp.tls_mode,
            "secure_transport": smtp.allows_sensitive,
        },
        "browser_push": {
            "configured": push.configured,
            "subject": push.subject,
            "public_key": push.public_key,
        },
        "providers": providers,
        "provider_locks": provider_locks,
        "real_ip": {
            **real_ip,
            "locked": {
                "header": handler.has("NGINX_REALIP_HEADER"),
                "trusted_proxies": handler.has("NGINX_REALIP_TRUSTED_PROXIES"),
            },
        },
        "oidc": {
            "issuer_url": None if oidc_locks["issuer_url"] else oidc.issuer_url,
            "client_id": None if oidc_locks["client_id"] else oidc.client_id,
            "scopes": None if oidc_locks["scopes"] else oidc.scopes,
            "redirect_uri": None if oidc_locks["redirect_uri"] else oidc.redirect_uri,
            "groups_claim": None if oidc_locks["groups_claim"] else oidc.groups_claim,
            "admin_group": None if oidc_locks["admin_group"] else oidc.admin_group,
            "user_match_field": None
            if oidc_locks["user_match_field"]
            else (oidc.user_match_field or "email"),
            "enabled": oidc.enabled,
            "default_login_method": oidc.default_login_method
            if oidc.default_login_method in {"local", "sso"}
            else "local",
            "login_button_text": oidc.login_button_text.strip() or "Continue with SSO",
            "allow_new_users": oidc.allow_new_users,
            "client_secret_configured": bool(oidc.client_secret),
            "named_providers": named,
            "locked_fields": {
                **{f"oidc_{name}": locked for name, locked in oidc_locks.items()},
                "oidc_allow_new_users": handler.has("OIDC_ISSUER_URL"),
                "oidc_enabled": False,
            },
        },
    }


@router.get("")
async def read_deployment_settings(
    db: AsyncSession = _DB_DEFAULT, admin: User = _ADMIN_DEFAULT
) -> dict:
    return await get_deployment_settings(db, admin)


def _normalize_oidc_provider(
    item: Any,
    existing: dict[str, dict[str, Any]],
    slugs: set[str],
    effective_oidc_enabled: bool,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise HTTPException(400, "Invalid OIDC provider entry.")
    slug = str(item.get("slug", "")).strip().lower()
    name = str(item.get("name", "")).strip()
    issuer = str(item.get("issuer_url", "")).strip()
    client_id = str(item.get("client_id", "")).strip()
    invalid_slug = not slug or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in slug
    )
    missing_credentials = effective_oidc_enabled and not (issuer and client_id)
    if not name or invalid_slug or missing_credentials or slug in slugs:
        raise HTTPException(
            400, "Each OIDC provider needs a unique name, slug, issuer and client ID."
        )
    if item.get("user_match_field", "email") not in {"email", "username"}:
        raise HTTPException(400, "OIDC user matching must be email or username.")
    secret = item.get("client_secret") or existing.get(slug, {}).get("client_secret")
    if not secret and effective_oidc_enabled:
        raise HTTPException(
            400,
            f"Client secret is required for OIDC provider '{name}' while OIDC is enabled.",
        )
    if item.get("client_secret"):
        secret = encrypt_secret(str(item["client_secret"]))
    return {
        **item,
        "slug": slug,
        "name": name,
        "issuer_url": issuer,
        "client_id": client_id,
        "client_secret": secret,
        "enabled": bool(item.get("enabled", True)),
    }


def _normalize_oidc_providers(
    value: str | None, oidc: OidcSettings, effective_oidc_enabled: bool
) -> str:
    try:
        incoming = json.loads(value or "[]")
    except ValueError as exc:
        raise HTTPException(400, "Invalid OIDC provider configuration JSON.") from exc
    if not isinstance(incoming, list) or len(incoming) > 20:
        raise HTTPException(400, "OIDC provider list must contain 0–20 providers.")
    existing = {p.get("slug"): p for p in _provider_rows(oidc)}
    normalized = []
    slugs: set[str] = set()
    for item in incoming:
        provider = _normalize_oidc_provider(item, existing, slugs, effective_oidc_enabled)
        normalized.append(provider)
        slugs.add(provider["slug"])
    if effective_oidc_enabled:
        incomplete = [
            item["name"]
            for item in normalized
            if not item["issuer_url"] or not item["client_id"] or not item["client_secret"]
        ]
        if incomplete:
            raise HTTPException(
                400,
                "OIDC is enabled, so every enabled provider must have an issuer, client ID, and client secret: "
                + ", ".join(incomplete),
            )
    return json.dumps(normalized)


def _update_oidc_field(oidc: OidcSettings, field: str, value: Any) -> None:
    if field == "oidc_enabled":
        oidc.enabled = bool(value)
    elif field == "oidc_client_secret":
        if value:
            oidc.client_secret = encrypt_secret(value)
    elif field == "oidc_user_match_field":
        if value not in {"email", "username"}:
            raise HTTPException(400, "OIDC user matching must be email or username.")
        oidc.user_match_field = value
    elif field == "oidc_default_login_method":
        if value not in {"local", "sso"}:
            raise HTTPException(400, "Default login method must be local or sso.")
        oidc.default_login_method = value
    elif field == "oidc_login_button_text":
        text = (value or "").strip()
        if not text or len(text) > 100:
            raise HTTPException(400, "SSO button text must be 1–100 characters.")
        oidc.login_button_text = text
    elif field == "oidc_allow_new_users":
        oidc.allow_new_users = bool(value)
    elif value is not None:
        setattr(oidc, field.removeprefix("oidc_"), value or None)


_NGINX_FIELDS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "nginx_realip_header": ("NGINX_REALIP_HEADER", validate_real_ip_header),
    "nginx_realip_trusted_proxies": ("NGINX_REALIP_TRUSTED_PROXIES", validate_trusted_proxies),
    **{
        attribute: (name, partial(validate_nginx_value, attribute))
        for name, attribute in NGINX_TLS_FIELDS.items()
    },
}


def _update_nginx_field(
    app: AppIntegrationSettings, handler: EnvConfigHandler, field: str, value: Any
) -> None:
    env_name, validator = _NGINX_FIELDS[field]
    if handler.has(env_name):
        raise HTTPException(
            409, f"{field} is managed by the deployment environment and cannot be changed here."
        )
    if value is not None or field in NGINX_TLS_FIELDS.values():
        try:
            setattr(app, field, validator(value))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.put("")
async def update_deployment_settings(
    payload: DeploymentSettingsRequest,
    db: AsyncSession = _DB_DEFAULT,
    admin: User = _ADMIN_DEFAULT,
) -> dict:
    app = await get_or_create_app_integration_settings(db)
    oidc = await get_or_create_oidc_settings(db)
    handler = EnvConfigHandler()
    before_nginx = await lock_nginx_configuration(db, app, handler)
    provider_locks = {
        field: handler.has(env_name) for field, env_name in _PROVIDER_ENV_NAMES.items()
    }
    locked_fields = {
        f"oidc_{name}": handler.has(env_name) for name, env_name in _OIDC_ENV_NAMES.items()
    }
    locked_fields["oidc_allow_new_users"] = handler.has("OIDC_ISSUER_URL")
    locked_fields["oidc_enabled"] = False
    effective_oidc_enabled = bool(
        oidc.enabled if payload.oidc_enabled is None else payload.oidc_enabled
    )
    for field, value in payload.model_dump(exclude_unset=True, exclude={"reload_nginx"}).items():
        if field in _NGINX_FIELDS:
            _update_nginx_field(app, handler, field, value)
            continue
        if (
            provider_locks.get(field)
            or field in _OIDC_ENV_LOCKED_FIELDS
            and locked_fields.get(field)
        ):
            raise HTTPException(
                409, f"{field} is managed by the deployment environment and cannot be changed here."
            )
        value = _validate_provider_value(field, value)
        if field == "oidc_providers_json":
            oidc.providers_json = _normalize_oidc_providers(value, oidc, effective_oidc_enabled)
        elif field.startswith("oidc_"):
            _update_oidc_field(oidc, field, value)
        elif field in _SECRET_FIELDS:
            if field in {"smtp_password", "web_push_vapid_private_key"} and value is None:
                setattr(app, field, None)
            elif value:
                setattr(app, field, encrypt_secret(value))
        elif field in _SAFE_PROVIDER_FIELDS:
            setattr(app, field, value or None)
    try:
        after_nginx = effective_nginx_configuration(app, handler)
        requested = payload.reload_nginx or before_nginx != after_nginx
        async with apply_nginx_change(after_nginx if requested else None):
            await db.commit()
    except (ValueError, NginxActivationError) as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
    apply_deployment_provider_credentials(app)
    return await get_deployment_settings(db, admin)
