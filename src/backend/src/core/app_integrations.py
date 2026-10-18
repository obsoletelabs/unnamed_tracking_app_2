"""Deployment-wide (not per-user) integration credentials singleton —
lives here rather than in api/routes/settings.py so both route handlers
and background features (features/metadata/refresh.py) can depend on it
without a route module importing another route module."""

from __future__ import annotations

from dataclasses import dataclass

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
    """Resolve the shared public HTTPS origin with ENV precedence over the saved setting."""
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


UPLOAD_LIMIT_FIELDS = (
    "max_upload_size_mb",
    "max_doc_size_mb",
    "max_save_archive_size_mb",
    "max_clip_size_mb",
    "max_world_save_size_mb",
)

_MAX_UPLOAD_LIMIT_MB = 2147483647


@dataclass(frozen=True)
class UploadLimit:
    """One effective upload cap and where it came from.

    ``source`` is ``environment`` (explicit deployment value, authoritative and locked),
    ``override`` (saved by an administrator) or ``default``.
    """

    field: str
    value: int
    source: str
    env_name: str
    environment_invalid: bool = False

    @property
    def locked(self) -> bool:
        return self.source == "environment"


def _environment_limit(handler: EnvConfigHandler, env_name: str) -> int | None:
    raw = handler.environ.get(env_name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if 1 <= value <= _MAX_UPLOAD_LIMIT_MB else None


async def get_upload_limit_details(db: AsyncSession) -> dict[str, UploadLimit]:
    """Resolve each cap: explicit environment value, then stored override, then default.

    An environment value that is set but not a whole number of megabytes is ignored (and
    reported) rather than silently used, so the stored override or default still applies.
    """
    row = await get_or_create_app_integration_settings(db)
    handler = EnvConfigHandler()
    details: dict[str, UploadLimit] = {}
    for name in UPLOAD_LIMIT_FIELDS:
        env_name = name.upper()
        from_env = _environment_limit(handler, env_name)
        invalid = from_env is None and handler.has(env_name)
        override = getattr(row, name)
        if from_env is not None:
            details[name] = UploadLimit(name, from_env, "environment", env_name)
        elif override:
            details[name] = UploadLimit(name, int(override), "override", env_name, invalid)
        else:
            default = int(getattr(settings, env_name))
            details[name] = UploadLimit(name, default, "default", env_name, invalid)
    return details


async def get_max_upload_size_mb(db: AsyncSession) -> int:
    """The effective image/media upload cap (see ``get_upload_limit_details``)."""
    return (await get_upload_limit_details(db))["max_upload_size_mb"].value


async def get_upload_limits_mb(db: AsyncSession) -> dict[str, int]:
    """Effective value of every upload cap."""
    return {name: limit.value for name, limit in (await get_upload_limit_details(db)).items()}
