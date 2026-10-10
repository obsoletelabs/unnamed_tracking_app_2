"""User routing controls for registered external notification providers."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_admin, get_current_user
from src.core.config import settings
from src.core.preferences import load_preferences
from src.core.public_url import normalize_public_url
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User
from src.database.session import get_db
from src.features.notification_browser import (
    BrowserSubscription,
    bound_subscription,
    browser_context,
    browser_session,
)
from src.features.notification_browser_enrollment import enroll_browser, update_browser
from src.features.notification_destinations import (
    resolve_destinations,
    retire_provider_destinations,
    withdraw_provider_work,
)
from src.features.notification_enrollment import (
    EnrollmentError,
    confirm_verification,
    create_email,
    request_verification,
    revoke_email,
    update_email,
)
from src.features.notification_policy import Trust
from src.features.notification_providers.registry import get_notification_providers
from src.features.notification_push_config import (
    PUSH_PROVIDER,
    PushKeyEnvironmentLocked,
    ensure_vapid_key,
)
from src.features.notification_settings import routing_settings
from src.features.notification_tests import queue_destination_test, test_smtp
from src.features.notification_webhooks import create_webhook, remove_webhook, update_webhook
from src.features.smtp_configuration import SMTP_PROVIDER, normalize_email
from src.plugin_api.notification_contracts import NotificationProviderDefinition
from src.plugin_api.runtime_client import PluginRuntimeRequestError, PluginRuntimeUnavailable

_NOTIFICATION_DB = Depends(get_db)
_NOTIFICATION_USER = Depends(get_current_user)

router = APIRouter(prefix="/api/settings/notification-providers", tags=["settings"])


@router.post("/browser-configuration/key")
async def generate_browser_push_key(
    db: AsyncSession = _NOTIFICATION_DB,
    _admin: User = Depends(get_current_admin),
) -> dict:
    try:
        return await ensure_vapid_key(db)
    except PushKeyEnvironmentLocked as exc:
        raise HTTPException(409, str(exc)) from exc


class ProviderUpdate(BaseModel):
    enabled: bool


class EmailCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=3, max_length=254)
    label: str = Field(default="Email", max_length=80)


class BrowserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription: BrowserSubscription
    public_key: str = Field(min_length=87, max_length=88)
    label: str = Field(default="This browser", max_length=80)


class BrowserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None


class EmailUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str | None = Field(default=None, min_length=3, max_length=254)
    label: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    recovery_allowed: bool | None = None


class PluginDestinationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    kind: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9][a-z0-9._-]*$")


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(min_length=3, max_length=128)
    url: str = Field(min_length=1, max_length=512)
    label: str = Field(default="Discord webhook", max_length=80)


class WebhookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    share_followed_media: bool | None = None


class SmtpTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=3, max_length=254)

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        return normalize_email(value)


class VerificationConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{8}$")


class DestinationUrlUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notification_url: str = Field(max_length=2048)

    @field_validator("notification_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return normalize_public_url(value)


@router.patch("/destinations/{destination_id}/url")
async def update_destination_url(
    destination_id: UUID,
    payload: DestinationUrlUpdate,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    destination = await db.scalar(
        select(NotificationDestination)
        .where(
            NotificationDestination.id == destination_id,
            NotificationDestination.user_id == current_user.id,
            NotificationDestination.active.is_(True),
            NotificationDestination.channel_context == "external",
        )
        .with_for_update()
    )
    if destination is None:
        raise HTTPException(404, "External notification destination not found")
    # Link preference changes do not prove possession or change destination trust.
    destination.notification_url = payload.notification_url or None
    await db.commit()
    return {"updated": True}


_Result = TypeVar("_Result")


async def _enrollment(db: AsyncSession, operation: Awaitable[_Result]) -> _Result:
    try:
        return await operation
    except EnrollmentError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400, detail="Enter a valid notification destination"
        ) from exc
    except PluginRuntimeUnavailable as exc:
        await db.rollback()
        raise HTTPException(503, "PWA infrastructure is temporarily unavailable") from exc


@router.get("/browser-configuration")
async def get_browser_push_configuration(
    request: Request,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> JSONResponse:
    context = await _enrollment(db, browser_context(db))
    session = await browser_session(db, request, current_user.id)
    current = await db.scalar(
        select(NotificationDestination.id).where(
            NotificationDestination.user_id == current_user.id,
            NotificationDestination.provider_id == PUSH_PROVIDER,
            NotificationDestination.configuration_ref == (str(session.id) if session else ""),
            NotificationDestination.active.is_(True),
        )
    )
    return JSONResponse(
        {
            "enabled": context is not None,
            "session_authenticated": session is not None,
            "public_key": context.configuration.public_key if context else "",
            "current_destination_id": str(current) if current else None,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/browser-destinations", status_code=201)
async def create_browser_destination(
    request: Request,
    payload: BrowserCreate,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    identity = await _enrollment(
        db,
        enroll_browser(
            db,
            request,
            current_user.id,
            payload.subscription,
            public_key=payload.public_key,
            label=payload.label,
        ),
    )
    return {"id": str(identity)}


@router.patch("/browser-destinations/{destination_id}")
async def update_browser_destination(
    destination_id: UUID,
    payload: BrowserUpdate,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    await _enrollment(
        db,
        update_browser(db, current_user.id, destination_id, payload.model_dump(exclude_none=True)),
    )
    return {"updated": True}


@router.delete("/browser-destinations/{destination_id}")
async def remove_browser_destination(
    destination_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    await _enrollment(db, update_browser(db, current_user.id, destination_id, {"remove": True}))
    return {"removed": True}


@router.get("/browser-destinations/{destination_id}/status")
async def browser_destination_status(
    request: Request,
    destination_id: UUID,
    revision: int = Query(ge=1),
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> JSONResponse:
    endpoint = await db.scalar(
        select(NotificationDestination).where(
            NotificationDestination.id == destination_id,
            NotificationDestination.user_id == current_user.id,
            NotificationDestination.provider_id == PUSH_PROVIDER,
            NotificationDestination.revision == revision,
        )
    )
    session = await browser_session(db, request, current_user.id)
    context = await _enrollment(db, browser_context(db))
    setting = await db.scalar(
        select(NotificationProviderSetting.enabled).where(
            NotificationProviderSetting.user_id == current_user.id,
            NotificationProviderSetting.provider_id == PUSH_PROVIDER,
        )
    )
    preferences = await load_preferences(db, current_user.id)
    enabled = bool(
        endpoint
        and session
        and context
        and setting
        and endpoint.configuration_ref == str(session.id)
        and preferences.get("notification_destinations", {}).get(str(destination_id)) is not False
        and PUSH_PROVIDER
        not in {p.strip() for p in settings.NOTIFICATION_BLOCKED_PROVIDERS.split(",")}
        and settings.NOTIFICATION_MINIMUM_TRUST < 2
        and await bound_subscription(db, endpoint, context) is not None
    )
    return JSONResponse(
        {
            "enabled": enabled,
            "generation": context.generation if context and enabled else None,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/webhook-destinations", status_code=201)
async def create_webhook_destination(
    payload: WebhookCreate,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    destination_id = await _enrollment(
        db, create_webhook(db, current_user.id, payload.provider_id, payload.url, payload.label)
    )
    return {"id": str(destination_id)}


@router.patch("/webhook-destinations/{destination_id}")
async def update_webhook_destination(
    destination_id: UUID,
    payload: WebhookUpdate,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    await _enrollment(
        db,
        update_webhook(db, current_user.id, destination_id, payload.model_dump(exclude_none=True)),
    )
    return {"updated": True}


@router.delete("/webhook-destinations/{destination_id}")
async def remove_webhook_destination(
    destination_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    await _enrollment(db, remove_webhook(db, current_user.id, destination_id))
    return {"removed": True}


@router.post("/smtp/test")
async def send_smtp_test(
    payload: SmtpTestRequest,
    db: AsyncSession = _NOTIFICATION_DB,
    current_admin: User = Depends(get_current_admin),
) -> dict:
    return await _enrollment(db, test_smtp(db, current_admin.id, payload.address))


@router.post("/destinations/{destination_id}/test")
async def send_destination_test(
    destination_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    return await _enrollment(db, queue_destination_test(db, current_user.id, destination_id))


@router.post("/email-destinations", status_code=201)
async def create_email_destination(
    payload: EmailCreate,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    destination_id = await _enrollment(
        db, create_email(db, current_user.id, payload.address, payload.label)
    )
    return {"id": str(destination_id)}


@router.patch("/email-destinations/{destination_id}")
async def update_email_destination(
    destination_id: UUID,
    payload: EmailUpdate,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    await _enrollment(
        db, update_email(db, current_user.id, destination_id, payload.model_dump(exclude_none=True))
    )
    return {"updated": True}


@router.delete("/email-destinations/{destination_id}")
async def remove_email_destination(
    destination_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    await _enrollment(db, revoke_email(db, current_user.id, destination_id, remove=True))
    return {"removed": True}


@router.post("/email-destinations/{destination_id}/verification")
async def request_email_verification(
    destination_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    challenge_id = await _enrollment(db, request_verification(db, current_user.id, destination_id))
    return {"challenge_id": str(challenge_id), "expires_in": 600}


@router.post("/email-destinations/{destination_id}/verification/confirm")
async def confirm_email_verification(
    destination_id: UUID,
    payload: VerificationConfirm,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    verified = await _enrollment(
        db,
        confirm_verification(
            db, current_user.id, destination_id, payload.challenge_id, payload.code
        ),
    )
    if not verified:
        raise HTTPException(status_code=400, detail="The code is invalid, expired or already used")
    return {"verified": True}


@router.post("/email-destinations/{destination_id}/verification/revoke")
async def revoke_email_verification(
    destination_id: UUID,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    await _enrollment(db, revoke_email(db, current_user.id, destination_id))
    return {"revoked": True}


@router.post("/plugin-destinations", status_code=201)
async def create_plugin_destination(
    payload: PluginDestinationCreate,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    """Enroll one declared plugin destination without accepting endpoint credentials."""
    providers = await get_notification_providers(db)
    provider = providers.get(payload.provider_id)
    registration = getattr(provider, "registration", None)
    if (
        provider is None
        or registration is None
        or registration.transport != "plugin"
        or not registration.definition
    ):
        raise HTTPException(404, "Generic notification provider not found")
    try:
        if not await provider.is_authorized(db, current_user.id):
            raise HTTPException(403, "Notification provider is not available to this account")
    except PluginRuntimeUnavailable as exc:
        raise HTTPException(
            503, "Notification provider runtime is temporarily unavailable"
        ) from exc

    definition = NotificationProviderDefinition.model_validate(registration.definition)
    choice = next((item for item in definition.destinations if item.kind == payload.kind), None)
    if choice is None:
        raise HTTPException(404, "Notification destination type not declared by this provider")
    if choice.fields:
        raise HTTPException(
            422,
            "This destination requires provider-specific fields; use the provider settings page.",
        )

    endpoint_key = f"plugin:{choice.kind}"
    endpoint = await db.scalar(
        select(NotificationDestination)
        .where(
            NotificationDestination.user_id == current_user.id,
            NotificationDestination.provider_id == registration.provider_id,
            NotificationDestination.endpoint_key == endpoint_key,
        )
        .with_for_update()
    )
    try:
        result = await provider._runtime.action(
            registration.plugin_id,
            definition.configure_action,
            {},
            user_id=str(current_user.id),
        )
    except PluginRuntimeUnavailable as exc:
        raise HTTPException(
            503, "Notification provider runtime is temporarily unavailable"
        ) from exc
    except PluginRuntimeRequestError as exc:
        if "Link and verify your Discord account" in exc.detail:
            raise HTTPException(
                409,
                "Link and verify your Discord account before adding this destination",
            ) from exc
        raise HTTPException(400, "Provider rejected destination configuration") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise HTTPException(400, "Provider rejected destination configuration")

    if endpoint is not None and endpoint.active and endpoint.enabled:
        return {"id": str(endpoint.id), "kind": endpoint.kind, "existing": True}
    if endpoint is None:
        destination_id = uuid4()
        endpoint = NotificationDestination(
            id=destination_id,
            user_id=current_user.id,
            provider_id=registration.provider_id,
            endpoint_key=endpoint_key,
            kind=choice.kind,
            channel_context="external",
            privacy=int(Trust.PRIVATE if choice.privacy == "PRIVATE" else Trust.PUBLIC),
            enabled=True,
            active=True,
            installation_id=registration.installation_id,
            configuration_ref=str(destination_id),
            display_name=choice.label,
        )
        db.add(endpoint)
    else:
        endpoint.revision += 1
        endpoint.active = True
        endpoint.enabled = True
        endpoint.verified_revision = None
        endpoint.verification_method = None
        endpoint.verification_revoked_at = None
        endpoint.media_consent_revision = None
        endpoint.media_consent_at = None
        endpoint.installation_id = registration.installation_id
        endpoint.configuration_ref = str(endpoint.id)
        endpoint.kind = choice.kind
        endpoint.channel_context = "external"
        endpoint.privacy = int(Trust.PRIVATE if choice.privacy == "PRIVATE" else Trust.PUBLIC)
        endpoint.display_name = choice.label
    await db.commit()
    return {"id": str(endpoint.id), "kind": endpoint.kind, "existing": False}


@router.get("/destinations")
async def list_notification_destinations(
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    return await routing_settings(db, current_user.id)


async def _setting(db: AsyncSession, user_id, provider_id: str) -> NotificationProviderSetting:
    row = await db.scalar(
        select(NotificationProviderSetting).where(
            NotificationProviderSetting.user_id == user_id,
            NotificationProviderSetting.provider_id == provider_id,
        )
    )
    if row is None:
        row = NotificationProviderSetting(
            user_id=user_id,
            provider_id=provider_id,
            enabled=False,
        )
        db.add(row)
        await db.flush()
    return row


@router.get("")
async def list_notification_provider_settings(
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> list[dict]:
    providers = await get_notification_providers(db)
    result = []
    for provider_id, provider in sorted(providers.items()):
        row = await _setting(db, current_user.id, provider_id)
        result.append(
            {
                "id": provider_id,
                "name": provider.name,
                "enabled": row.enabled,
                "kind": "builtin" if provider_id in {SMTP_PROVIDER, PUSH_PROVIDER} else "plugin",
            }
        )
    await db.commit()
    return result


@router.put("/{provider_id}")
async def update_notification_provider_setting(
    provider_id: str,
    payload: ProviderUpdate,
    db: AsyncSession = _NOTIFICATION_DB,
    current_user: User = _NOTIFICATION_USER,
) -> dict:
    providers = await get_notification_providers(db)
    provider = providers.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Unknown notification provider.")
    row = await _setting(db, current_user.id, provider_id)
    row.enabled = payload.enabled
    await resolve_destinations(db, current_user.id)
    registration = getattr(provider, "registration", None)
    if not payload.enabled and registration is None:
        await withdraw_provider_work(db, provider_id, current_user.id)
    if registration is not None:
        if not payload.enabled:
            await retire_provider_destinations(db, provider_id, current_user.id)
        await db.execute(
            update(NotificationDestination)
            .where(
                NotificationDestination.user_id == current_user.id,
                NotificationDestination.provider_id == provider_id,
                NotificationDestination.installation_id == registration.installation_id,
                or_(
                    NotificationDestination.kind == "legacy_webhook",
                    NotificationDestination.encrypted_configuration.is_not(None),
                    NotificationDestination.configuration_ref.is_not(None),
                ),
            )
            .values(enabled=payload.enabled, active=payload.enabled)
        )
    await db.commit()
    return {
        "id": provider_id,
        "name": provider.name,
        "enabled": row.enabled,
        "kind": "builtin" if provider_id in {SMTP_PROVIDER, PUSH_PROVIDER} else "plugin",
    }
