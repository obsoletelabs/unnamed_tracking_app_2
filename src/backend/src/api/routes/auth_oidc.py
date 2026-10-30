"""Select configured OIDC providers and validate their identities before creating sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count
from starlette.responses import RedirectResponse

from src.core.auth import (
    SESSION_TTL_SECONDS,
    hash_password,
    session_cookie_name,
    username_in_use,
    validate_display_name,
    validate_username,
)
from src.core.config import settings
from src.core.crypto import decrypt_secret
from src.core.metrics import (
    AUTH_ATTEMPTS,
    AUTH_SESSION_TRANSITIONS,
    OIDC_FAILURES,
    OIDC_STAGE_DURATION,
)
from src.core.oidc import (
    OidcConfig,
    begin_oidc,
    env_oidc_config,
    oauth,
    register_oidc_provider,
    validate_oidc_metadata,
)
from src.core.password_work import run_password_job
from src.core.session_manager import create_session
from src.database.models.oidc_settings import OidcSettings
from src.database.models.user import User
from src.database.session import get_db
from src.features.smtp_configuration import normalize_email

router = APIRouter(prefix="/api/auth/oidc", tags=["auth"])
logger = logging.getLogger(__name__)


@contextmanager
def _oidc_stage(stage: str) -> Iterator[None]:
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except Exception:
        outcome = "error"
        raise
    finally:
        OIDC_STAGE_DURATION.labels(stage, outcome).observe(time.perf_counter() - started)


def _oidc_terminal(outcome: str, *, failure_reason: str | None = None) -> None:
    AUTH_ATTEMPTS.labels("oidc", outcome).inc()
    if failure_reason is not None:
        OIDC_FAILURES.labels(failure_reason).inc()


def _env_config() -> OidcConfig | None:
    return env_oidc_config()


def _named_rows(row: OidcSettings, *, include_disabled: bool = False) -> list[dict[str, Any]]:
    try:
        data = json.loads(row.providers_json or "[]")
    except (TypeError, ValueError):
        return []
    return [
        provider
        for provider in data
        if isinstance(provider, dict)
        and provider.get("slug")
        and (include_disabled or provider.get("enabled", True))
    ]


def _config_from_provider(provider: dict[str, Any]) -> OidcConfig:
    issuer = str(provider["issuer_url"]).strip()
    return OidcConfig(
        issuer_url=issuer,
        client_id=str(provider["client_id"]),
        client_secret=decrypt_secret(str(provider["client_secret"])),
        scopes=provider.get("scopes") or "openid profile email",
        redirect_uri=provider.get("redirect_uri") or None,
        groups_claim=provider.get("groups_claim") or "groups",
        admin_group=provider.get("admin_group") or None,
        user_match_field=provider.get("user_match_field") or "email",
        allow_new_users=bool(provider.get("allow_new_users", True)),
        require_verified_email=provider.get("require_verified_email", False) is True,
        discovery_url=issuer if issuer.endswith("/.well-known/openid-configuration") else None,
        name=provider.get("name") or provider["slug"],
        slug=provider["slug"],
        button_text=provider.get("button_text") or "Continue with SSO",
        button_image_url=provider.get("button_image_url"),
    )


def _provider_key(issuer: str, client_id: str) -> tuple[str, str]:
    return issuer.strip().removesuffix("/.well-known/openid-configuration").rstrip("/"), client_id


def _with_verification_policy(
    config: OidcConfig | None, row: OidcSettings | None
) -> OidcConfig | None:
    """The same issuer/client must not bypass a requirement through another callback alias."""
    if config is None:
        return None

    selected = _provider_key(config.issuer_url, config.client_id)
    required = config.require_verified_email
    if row is not None:
        required = required or (
            row.require_verified_email
            and selected == _provider_key(row.issuer_url or "", row.client_id or "")
        )
        required = required or any(
            provider.get("require_verified_email", False) is True
            and selected
            == _provider_key(
                str(provider.get("issuer_url", "")), str(provider.get("client_id", ""))
            )
            for provider in _named_rows(row, include_disabled=True)
        )
    environment = env_oidc_config()
    required = required or bool(
        environment
        and environment.require_verified_email
        and selected == _provider_key(environment.issuer_url, environment.client_id)
    )
    return replace(config, require_verified_email=required)


def _named_provider_config(
    row: OidcSettings, slug: str, require_autostart: bool
) -> OidcConfig | None:
    for provider in _named_rows(row):
        if provider.get("slug") == slug and provider.get("client_secret"):
            if require_autostart and provider.get("autostart_enabled", True) is False:
                return None
            return _config_from_provider(provider)
    return None


async def _load_config(
    db: AsyncSession, slug: str = "default", require_autostart: bool = False
) -> OidcConfig | None:
    row = await db.scalar(select(OidcSettings).limit(1))
    if row and not row.enabled:
        return None
    if row and slug != "default":
        return _with_verification_policy(_named_provider_config(row, slug, require_autostart), row)

    environment_config = _env_config()
    if environment_config is not None:
        return _with_verification_policy(environment_config, row)

    if row and row.issuer_url and row.client_id and row.client_secret:
        issuer = row.issuer_url.strip()
        config = OidcConfig(
            issuer_url=issuer,
            client_id=row.client_id,
            client_secret=decrypt_secret(row.client_secret),
            scopes=row.scopes or "openid profile email",
            redirect_uri=getattr(row, "redirect_uri", None),
            groups_claim=row.groups_claim or "groups",
            admin_group=row.admin_group,
            user_match_field=row.user_match_field or "email",
            allow_new_users=row.allow_new_users,
            require_verified_email=row.require_verified_email,
        )
        return _with_verification_policy(config, row)
    return None


async def _get_config(
    db: AsyncSession, request: Request, slug: str = "default", require_autostart: bool = False
) -> OidcConfig | None:
    config = await _load_config(db, slug, require_autostart)
    if config is None:
        return None
    environment = env_oidc_config()
    if (
        environment
        and environment.redirect_uri
        and _provider_key(environment.issuer_url, environment.client_id)
        == _provider_key(config.issuer_url, config.client_id)
    ):
        return replace(config, redirect_uri=environment.redirect_uri)
    # Saved UI values are generated previews, not a pin to the administrator's
    # browser address. Resolve the route from this sign-in request, including its
    # scheme, host, port and proxy mount path. An explicit ENV override above is optional.
    callback = (
        request.url_for("oidc_callback_provider", provider_slug=slug)
        if slug != "default"
        else request.url_for("oidc_callback")
    )
    return replace(config, redirect_uri=str(callback))


@router.get("/status")
async def oidc_status(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    row = await db.scalar(select(OidcSettings).limit(1))
    config = await _get_config(db, request)
    providers = []
    oidc_master_enabled = row.enabled if row is not None else config is not None
    if row and oidc_master_enabled:
        for provider in _named_rows(row):
            if provider.get("show_on_login", True) is False:
                continue
            providers.append(
                {
                    "name": provider.get("name", provider["slug"]),
                    "slug": provider["slug"],
                    "button_text": provider.get("button_text") or "Continue with SSO",
                    "button_image_url": provider.get("button_image_url"),
                    "button_color": provider.get("button_colour") or provider.get("button_color"),
                    "autostart_enabled": provider.get("autostart_enabled", True) is not False,
                }
            )
    if not providers and config:
        providers = [
            {
                "name": config.name,
                "slug": "default",
                "button_text": (
                    row.login_button_text.strip()
                    if row and row.login_button_text.strip()
                    else config.button_text
                ),
                "button_image_url": config.button_image_url,
            }
        ]
    return {
        "enabled": oidc_master_enabled and (config is not None or bool(providers)),
        "issuer": urlparse(config.issuer_url).hostname if config else None,
        "default_login_method": (
            row.default_login_method
            if row and row.default_login_method in {"local", "sso"}
            else "local"
        ),
        "login_button_text": (
            row.login_button_text.strip()
            if row and row.login_button_text.strip()
            else "Continue with SSO"
        ),
        "providers": providers,
    }


@router.get("/login", name="oidc_login")
async def oidc_login(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    config = await _get_config(db, request)
    if config is None:
        return RedirectResponse("/login?oidc_error=not_configured", 303)
    return await begin_oidc(request, config)


@router.get("/login/{provider_slug}")
async def oidc_provider_login(
    provider_slug: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    autostart: bool = True,
):
    config = await _get_config(db, request, provider_slug, require_autostart=autostart)
    if config is None:
        return RedirectResponse("/login?oidc_error=not_configured", 303)
    return await begin_oidc(request, config)


async def _fetch_oidc_token(request: Request, client: Any, config: OidcConfig) -> dict[str, Any]:
    params = {
        "code": request.query_params.get("code"),
        "state": request.query_params.get("state"),
    }
    state = params["state"]
    if not state:
        raise ValueError("Missing OIDC state parameter")
    state_data = await client.framework.get_state_data(request.session, state)
    if not state_data:
        raise ValueError("Invalid OIDC state parameter")
    await client.framework.clear_state_data(request.session, state)
    # Authlib exposes state/PKCE restoration only through this private adapter;
    # preserve the same saved parameters as its standard authorization-code flow.
    params = client._format_state_params(state_data, params)  # pylint: disable=protected-access
    with _oidc_stage("discovery"):
        issuer = await validate_oidc_metadata(client, config)
    with _oidc_stage("token"):
        token = await client.fetch_access_token(**params)
    id_token, nonce = token.get("id_token"), state_data.get("nonce")
    if not isinstance(id_token, str) or not id_token or not isinstance(nonce, str) or not nonce:
        raise ValueError("Missing OIDC ID token or nonce")
    with _oidc_stage("claims"):
        claims = dict(
            await client.parse_id_token(
                token,
                nonce=nonce,
                claims_options={"iss": {"essential": True, "value": issuer}},
            )
        )
    # Authlib allows nonce_supported=false to skip the nonce check. This host always
    # sends a nonce and requires the signed response to bind to that exact login.
    if claims.get("nonce") != nonce:
        raise ValueError("OIDC nonce mismatch")
    if not claims.get("email"):
        profile = dict(await client.userinfo(token=token))
        if not isinstance(profile.get("sub"), str) or profile["sub"] != claims.get("sub"):
            raise ValueError("OIDC UserInfo subject mismatch")
        for key in ("email", "preferred_username", "name", config.groups_claim):
            if key not in claims and key in profile:
                claims[key] = profile[key]
        # Verification belongs to the email returned by the same endpoint.
        claims["email_verified"] = profile.get("email_verified") is True
    email = claims.get("email")
    if not isinstance(email, str):
        raise ValueError("Invalid OIDC email claim")
    claims["email"] = normalize_email(email).lower()
    token["userinfo"] = claims
    return token


def _groups(claims: dict[str, Any], name: str) -> set[str]:
    value = claims.get(name)
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {item for item in value if isinstance(item, str) and item.strip()}
    return set()


def _match_value(claims: dict[str, Any], field: str, email: str) -> str:
    if field == "username":
        # Only the sign-in style claim may link an existing account. "name" is a free-form,
        # non-unique display value and must never select (merge into) an account.
        username = claims.get("preferred_username")
        return username.strip() if isinstance(username, str) else ""
    return email


def _claim_display_name(claims: dict[str, Any]) -> str | None:
    """Best-effort display name for a NEW account; invalid values are simply ignored."""
    try:
        return validate_display_name(str(claims.get("name") or ""))
    except ValueError:
        return None


def _safe_username(value: str, email: str) -> str:
    for candidate in (value, email.split("@", 1)[0]):
        username = "".join(
            char
            for char in candidate.strip()
            if char.isascii() and (char.isalnum() or char in "._-")
        )[:100]
        if username:
            return validate_username(username)
    return f"user-{secrets.token_hex(4)}"


async def _create_oidc_user(
    db: AsyncSession,
    claims: dict[str, Any],
    email: str,
    linked_subject: str,
    *,
    is_admin: bool,
) -> User:
    username = _safe_username(
        str(claims.get("preferred_username") or claims.get("name") or ""), email
    )
    base = username
    suffix = 1
    while await username_in_use(db, username):
        suffix += 1
        username = f"{base[: 100 - len(str(suffix)) - 1]}-{suffix}"
    user = User(
        username=username,
        display_name=_claim_display_name(claims),
        email=email,
        password_hash=await run_password_job(hash_password, secrets.token_urlsafe(48) + "A!a"),
        is_active=True,
        is_admin=is_admin,
        oidc_subject=linked_subject,
    )
    db.add(user)
    await db.flush()
    return user


async def _legacy_oidc_identity(db: AsyncSession, slug: str, subject: str) -> User | str | None:
    """Migrate existing bindings even when optional account matching is disabled."""
    candidates = [
        value for value in (subject, f"{slug}:{subject}") if not value.startswith("oidc-v2:")
    ]
    matches = User.oidc_subject.in_(candidates)
    total = int(await db.scalar(select(count()).select_from(User).where(matches)) or 0)
    if total > 1:
        return "identity_conflict"
    return await db.scalar(select(User).where(matches)) if total else None


async def _resolve_oidc_identity(
    db: AsyncSession, config: OidcConfig, claims: dict[str, Any]
) -> User | str:
    """Resolve, provision, or reject an identity before any session is created."""
    subject, email = claims.get("sub"), claims.get("email")
    issuer = claims.get("iss")
    if (
        not config.accepts_issuer(issuer)
        or not isinstance(subject, str)
        or not 0 < len(subject) <= 255
        or not isinstance(email, str)
        or not email.strip()
    ):
        return "identity_missing"
    email = email.strip().lower()
    # OIDC identities are the exact (issuer, opaque subject) pair. Slugs are
    # presentation/configuration names and can be renamed or point at a new issuer.
    identity = json.dumps([issuer, subject], separators=(",", ":"))
    linked_subject = "oidc-v2:" + hashlib.sha256(identity.encode()).hexdigest()
    user = await db.scalar(select(User).where(User.oidc_subject == linked_subject))
    if user is None:
        legacy = await _legacy_oidc_identity(db, config.slug, subject)
        if isinstance(legacy, str):
            return legacy
        user = legacy
    if user is None and config.user_match_field != "none":
        field = (
            config.user_match_field if config.user_match_field in {"email", "username"} else "email"
        )
        match = _match_value(claims, field, email)
        if not match:
            return "identity_missing"
        if field == "username":
            user = await db.scalar(select(User).where(User.username == match))
        else:
            user = await db.scalar(select(User).where(func.lower(User.email) == email))
            if user is not None and user.email_unverified and user.oidc_subject is None:
                # The user typed this address into their own profile; nobody proved it is theirs.
                return "email_unverified"

    email_matches = User.email.is_not(None) & (func.lower(User.email) == email)
    email_owner = await db.scalar(select(User.id).where(email_matches))
    if int(await db.scalar(select(count()).select_from(User).where(email_matches)) or 0) > 1 or (
        email_owner is not None and (user is None or email_owner != user.id)
    ):
        return "email_conflict"
    if user is None:
        if not config.allow_new_users:
            return "user_creation_disabled"
        result: User | str = await _create_oidc_user(
            db,
            claims,
            email,
            linked_subject,
            is_admin=bool(
                config.admin_group and config.admin_group in _groups(claims, config.groups_claim)
            ),
        )
    else:
        result = _update_oidc_user(user, config, claims, linked_subject, email)
    return result


def _update_oidc_user(
    user: User, config: OidcConfig, claims: dict[str, Any], linked_subject: str, email: str
) -> User | str:
    """Update the selected identity without overriding the verification setting."""
    subject = claims["sub"]
    if not user.is_active:
        return "account_disabled"
    if user.oidc_subject != linked_subject:
        legacy = (
            user.oidc_subject is not None
            and not user.oidc_subject.startswith("oidc-v2:")
            and user.oidc_subject in {f"{config.slug}:{subject}", subject}
        )
        if user.oidc_subject and not legacy:
            return "identity_conflict"
    # Linking only ever sets the subject/email. The sign-in username and the
    # user-chosen display name are never rewritten from provider claims.
    user.oidc_subject = linked_subject
    user.email = email
    user.email_unverified = False
    if config.admin_group:
        user.is_admin = config.admin_group in _groups(claims, config.groups_claim)

    return user


# Parallel routes/models intentionally share this shape.
# pylint: disable=duplicate-code
async def _complete_callback(
    request: Request, db: AsyncSession, config: OidcConfig, client_name: str
) -> RedirectResponse:
    try:
        if request.query_params.get("error") == "access_denied":
            raise ValueError("OIDC flow cancelled")
        register_oidc_provider(config, client_name)
        client = oauth.create_client(client_name)
        if client is None:
            _oidc_terminal("failed", failure_reason="provider_unavailable")
            return RedirectResponse("/login?oidc_error=provider_unavailable", 303)
        token = await _fetch_oidc_token(request, client, config)
        claims = dict(token["userinfo"])
    # Provider token/userinfo failures must return to sign-in without opening a session.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        if request.query_params.get("error") == "access_denied":
            _oidc_terminal("cancelled")
        else:
            logger.warning("OIDC callback validation failed: %s", type(exc).__name__)
            reason = "timeout" if isinstance(exc, TimeoutError) else "validation"
            _oidc_terminal("failed", failure_reason=reason)
        return RedirectResponse("/login?oidc_error=authentication_failed", 303)

    # Provider verification is checked before resolving or mutating any local account.
    if config.require_verified_email and claims.get("email_verified") is not True:
        _oidc_terminal("failed", failure_reason="verified_email")
        return RedirectResponse("/login?oidc_error=verified_email_required", 303)
    try:
        with _oidc_stage("account_link"):
            user = await _resolve_oidc_identity(db, config, claims)
            if isinstance(user, str):
                _oidc_terminal("failed", failure_reason="linking_policy")
                return RedirectResponse(f"/login?oidc_error={user}", 303)
            session_context = await create_session(db, user, request)
            session_token = session_context.token
            await db.commit()
    except IntegrityError:
        # A competing identity/account update can win after the availability lookup.
        # Roll back all linking/provisioning/session work without exposing database details.
        await db.rollback()
        _oidc_terminal("failed", failure_reason="linking_policy")
        return RedirectResponse("/login?oidc_error=identity_conflict", 303)
    _oidc_terminal("success")
    AUTH_SESSION_TRANSITIONS.labels("created").inc()
    response = RedirectResponse("/login?oidc=success", 303)
    response.set_cookie(
        key=session_cookie_name(request.headers.get("host", "")),
        value=session_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.AUTH_COOKIE_SECURE,
        path="/",
    )
    return response


# pylint: enable=duplicate-code


@router.get("/callback", name="oidc_callback")
async def oidc_callback(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    config = await _get_config(db, request)
    if config is None:
        _oidc_terminal("failed", failure_reason="not_configured")
        return RedirectResponse("/login?oidc_error=not_configured", 303)
    return await _complete_callback(request, db, config, "oidc")


@router.get("/callback/{provider_slug}", name="oidc_callback_provider")
async def oidc_callback_provider(
    provider_slug: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]
):
    config = await _get_config(db, request, provider_slug)
    if config is None:
        _oidc_terminal("failed", failure_reason="not_configured")
        return RedirectResponse("/login?oidc_error=not_configured", 303)
    return await _complete_callback(request, db, config, f"oidc_{provider_slug}")
