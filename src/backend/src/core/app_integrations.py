"""Deployment-wide (not per-user) integration credentials singleton —
lives here rather than in api/routes/settings.py so both route handlers
and background features (features/metadata/refresh.py) can depend on it
without a route module importing another route module."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.env_handler import EnvConfigHandler
from src.core.public_url import is_public_app_url, normalize_public_url
from src.database.models.app_integration_settings import AppIntegrationSettings


async def get_or_create_app_integration_settings(db: AsyncSession) -> AppIntegrationSettings:
    """Exactly one row for the whole deployment — created lazily, on first
    access, same as get_or_create_scan_settings but with no user_id since
    this isn't per-user."""
    row = await db.scalar(select(AppIntegrationSettings).limit(1))
    if row is None:
        row = AppIntegrationSettings()
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def get_public_app_url(db: AsyncSession) -> str:
    """Only a configured public FQDN acts as the shared notification URL default."""
    handler = EnvConfigHandler()
    if handler.has("PUBLIC_APP_URL"):
        value = handler.get("PUBLIC_APP_URL")
    else:
        row = await get_or_create_app_integration_settings(db)
        await db.refresh(row)
        value = row.public_app_url
    normalized = normalize_public_url(str(value or ""))
    return normalized if is_public_app_url(normalized) else ""


async def get_default_ui_style(db: AsyncSession) -> str:
    """Resolve the built-in presentation default without writing account choices."""
    handler = EnvConfigHandler()
    if handler.has("DEFAULT_UI_STYLE"):
        value = handler.get("DEFAULT_UI_STYLE")
    else:
        value = await db.scalar(select(AppIntegrationSettings.default_ui_style).limit(1))
    # archive-pocket is the existing account/API identifier for Modern.
    return "classic" if value == "classic" else "archive-pocket"


async def get_max_upload_size_mb(db: AsyncSession) -> int:
    """The effective image/media upload cap: whatever an admin set under
    Settings > Administration > Limits, falling back to the
    MAX_UPLOAD_SIZE_MB env default when nothing's been saved yet."""
    row = await get_or_create_app_integration_settings(db)
    return row.max_upload_size_mb or settings.MAX_UPLOAD_SIZE_MB


UPLOAD_LIMIT_FIELDS = (
    "max_upload_size_mb",
    "max_doc_size_mb",
    "max_save_archive_size_mb",
    "max_clip_size_mb",
    "max_world_save_size_mb",
)


async def get_upload_limits_mb(db: AsyncSession) -> dict[str, int]:
    """Resolve the four existing environment caps with deployment overrides."""
    row = await get_or_create_app_integration_settings(db)
    return {
        name: int(getattr(row, name) or getattr(settings, name.upper()))
        for name in UPLOAD_LIMIT_FIELDS
    }
