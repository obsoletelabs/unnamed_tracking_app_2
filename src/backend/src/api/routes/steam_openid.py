"""Redirect-based Steam identity link (OpenID 2.0). Grants no library access."""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.app_integrations import get_public_app_url
from src.core.auth import get_current_user
from src.core.public_url import normalize_public_url
from src.database.models.user import User
from src.database.session import get_db
from src.features.imports import steam_connection, steam_openid

router = APIRouter(prefix="/api/settings/steam/openid", tags=["steam-connections"])
_SETTINGS_PATH = "/settings"


class OpenIdStart(BaseModel):
    redirect_url: str


async def _app_origin(db: AsyncSession, request: Request) -> str:
    """The origin Steam sends the browser back to: configured URL, else the one in use."""
    configured = await get_public_app_url(db)
    if configured:
        return configured
    try:
        return normalize_public_url(request.headers.get("origin") or str(request.base_url))
    except ValueError:
        return normalize_public_url(str(request.base_url))


def _finish(**query: str) -> RedirectResponse:
    response = RedirectResponse(
        f"{_SETTINGS_PATH}?{urlencode({'section': 'connections', **query})}", status_code=303
    )
    response.headers["Cache-Control"] = "no-store"
    # Keep the one-time state in the callback URL out of any Referer header.
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/begin", response_model=OpenIdStart)
async def begin(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    url = await steam_openid.begin(db, current_user.id, await _app_origin(db, request))
    return JSONResponse({"redirect_url": url}, headers={"Cache-Control": "no-store"})


@router.get("/callback")
async def callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    try:
        await steam_openid.complete(db, current_user.id, dict(request.query_params))
    except steam_connection.SteamConnectionError as exc:
        return _finish(steam_openid="failed", steam_openid_error=steam_openid.error_code(exc))
    return _finish(steam_openid="linked")
