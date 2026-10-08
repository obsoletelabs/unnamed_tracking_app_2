"""User routing controls for registered external notification providers."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User
from src.database.session import get_db
from src.features.notification_destinations import resolve_destinations
from src.features.notification_enrollment import (
    EnrollmentError,
    confirm_verification,
    create_email,
    request_verification,
    revoke_email,
    update_email,
)
from src.features.notification_providers.registry import get_notification_providers
from src.features.notification_settings import routing_settings
from src.features.smtp_configuration import SMTP_PROVIDER

_NOTIFICATION_DB = Depends(get_db)
_NOTIFICATION_USER = Depends(get_current_user)

router = APIRouter(prefix="/api/settings/notification-providers", tags=["settings"])


class ProviderUpdate(BaseModel):
    enabled: bool


class EmailCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str = Field(min_length=3, max_length=254)
    label: str = Field(default="Email", max_length=80)


class EmailUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    address: str | None = Field(default=None, min_length=3, max_length=254)
    label: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    recovery_allowed: bool | None = None


class VerificationConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{8}$")


_Result = TypeVar("_Result")


async def _enrollment(db: AsyncSession, operation: Awaitable[_Result]) -> _Result:
    try:
        return await operation
    except EnrollmentError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Enter a valid email destination") from exc


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
                "kind": "builtin" if provider_id == SMTP_PROVIDER else "plugin",
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
    if registration is not None:
        await db.execute(
            update(NotificationDestination)
            .where(
                NotificationDestination.user_id == current_user.id,
                NotificationDestination.provider_id == provider_id,
                NotificationDestination.installation_id == registration.installation_id,
                NotificationDestination.active.is_(True),
            )
            .values(enabled=payload.enabled)
        )
    await db.commit()
    return {
        "id": provider_id,
        "name": provider.name,
        "enabled": row.enabled,
        "kind": "builtin" if provider_id == SMTP_PROVIDER else "plugin",
    }
