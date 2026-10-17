from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_admin, get_current_user
from src.core.env_handler import EnvConfigHandler
from src.core.real_ip import (
    get_effective_real_ip_config,
    get_real_ip_presets,
    validate_trusted_proxies,
)
from src.database.models.app_integration_settings import AppIntegrationSettings
from src.database.models.user import User
from src.database.session import get_db

router = APIRouter(prefix="/api/internal/real-ip", tags=["internal"])


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_PROXY_HEADERS = ("x-real-ip", "x-forwarded-for", "forwarded")


def _is_direct_local_request(request: Request) -> bool:
    """The container's own nginx renderer calls the backend directly on loopback.

    Requests relayed by nginx always carry the proxy headers it sets, so a remote
    caller cannot present as the local renderer.
    """
    host = request.client.host if request.client else None
    return host in _LOOPBACK_HOSTS and not any(name in request.headers for name in _PROXY_HEADERS)


@router.get("")
async def read_real_ip_config(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Trusted-proxy topology: the local nginx renderer, first-run setup or an administrator."""
    if not _is_direct_local_request(request) and (
        await db.scalar(select(User.id).limit(1)) is not None
    ):
        await get_current_admin(await get_current_user(request, db))
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
