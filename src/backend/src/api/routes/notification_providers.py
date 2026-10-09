"""User routing controls for registered external notification providers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User
from src.database.session import get_db
from src.features.notification_destinations import resolve_destinations
from src.features.notification_providers.registry import get_notification_providers

_NOTIFICATION_DB = Depends(get_db)
_NOTIFICATION_USER = Depends(get_current_user)

router = APIRouter(prefix="/api/settings/notification-providers", tags=["settings"])


class ProviderUpdate(BaseModel):
    enabled: bool


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
                "kind": "plugin",
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
        "kind": "plugin",
    }
