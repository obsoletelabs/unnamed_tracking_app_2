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
    """Resolve the shared public HTTPS origin with ENV precedence over the saved setting."""
    handler = EnvConfigHandler()
    if handler.has("PUBLIC_APP_URL"):
        value = handler.get("PUBLIC_APP_URL")
    else:
        value = await db.scalar(select(AppIntegrationSettings.public_app_url).limit(1))
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
    """The effective image/media upload cap (see `get_upload_limit_sources` for precedence)."""
    return (await get_upload_limits_mb(db))["max_upload_size_mb"]


UPLOAD_LIMIT_FIELDS = (
    "max_upload_size_mb",
    "max_doc_size_mb",
    "max_save_archive_size_mb",
    "max_clip_size_mb",
    "max_world_save_size_mb",
)


MAX_UPLOAD_LIMIT_MB = 2147483647


def _environment_limit(handler: EnvConfigHandler, name: str) -> int | None:
    """A whole-megabyte value the operator set explicitly, or None when absent or unusable."""
    env_name = name.upper()
    if not handler.has(env_name):
        return None
    value = handler.get(env_name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= MAX_UPLOAD_LIMIT_MB else None


async def get_upload_limit_sources(db: AsyncSession) -> dict[str, dict[str, object]]:
    """Each upload cap with the source that decided it.

    Precedence, highest first: an explicit, valid deployment environment value (locked: the
    admin UI cannot change it); the value an administrator saved; the built-in default. An
    environment value that is not a whole number of megabytes is ignored, never half-applied,
    and reported so the operator can fix it."""
    row = await get_or_create_app_integration_settings(db)
    handler = EnvConfigHandler()
    result: dict[str, dict[str, object]] = {}
    for name in UPLOAD_LIMIT_FIELDS:
        from_env = _environment_limit(handler, name)
        stored = getattr(row, name)
        if from_env is not None:
            value, source = from_env, "environment"
        elif stored:
            value, source = int(stored), "override"
        else:
            value, source = int(getattr(settings, name.upper())), "default"
        result[name] = {
            "value": value,
            "source": source,
            "locked": source == "environment",
            "environment_variable": name.upper(),
            "environment_value": from_env,
            "environment_invalid": handler.has(name.upper()) and from_env is None,
            "stored_override": int(stored) if stored else None,
        }
    return result


async def get_upload_limits_mb(db: AsyncSession) -> dict[str, int]:
    """The effective value of every upload cap."""
    sources = await get_upload_limit_sources(db)
    return {name: int(str(info["value"])) for name, info in sources.items()}
