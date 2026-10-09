from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.env_handler import EnvConfigHandler
from src.core.real_ip import (
    get_effective_real_ip_config,
    get_real_ip_presets,
    validate_trusted_proxies,
)
from src.database.models.app_integration_settings import AppIntegrationSettings
from src.database.session import get_db

router = APIRouter(prefix="/api/internal/real-ip", tags=["internal"])


@router.get("")
async def read_real_ip_config(db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.scalar(select(AppIntegrationSettings).limit(1))
    return get_effective_real_ip_config(
        EnvConfigHandler(),
        getattr(row, "nginx_realip_header", None),
        getattr(row, "nginx_realip_trusted_proxies", None),
    )


@router.get("/presets")
async def read_real_ip_presets() -> dict:
    return {"presets": get_real_ip_presets()}


class ProxyValidationRequest(BaseModel):
    value: str = Field(max_length=8192)


@router.post("/validate")
async def validate_proxy_entries(payload: ProxyValidationRequest) -> dict[str, str]:
    """Inert normalization for setup/settings; it exposes no stored configuration."""
    if len(payload.value.split()) > 256:
        raise HTTPException(422, "At most 256 proxy entries can be validated at once")
    try:
        return {"value": validate_trusted_proxies(payload.value)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
