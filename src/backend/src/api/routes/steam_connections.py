"""Private, actor-scoped Steam connection controls."""

import asyncio
import secrets
import time
from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features.imports import steam_connection, steam_openid
from src.features.metadata.games.steam import SteamLibraryError


class SteamRoute(APIRoute):
    """Never echo invalid password/code inputs in FastAPI validation errors."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse(
                    {"detail": "Invalid Steam connection request."}, status_code=400
                )
            except steam_connection.SteamConnectionError as exc:
                response = JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
            except SteamLibraryError as exc:
                response = JSONResponse({"detail": str(exc)}, status_code=502)
            response.headers["Cache-Control"] = "no-store"
            return response

        return safe_handler


router = APIRouter(prefix="/api/settings/steam", tags=["steam-connections"], route_class=SteamRoute)


class PendingRead(BaseModel):
    attempt_id: str
    expires_at: int
    interval: float
    challenge_url: str | None
    confirmation_types: list[int]
    phase: Literal["starting", "waiting", "checking"]


class ConnectionRead(BaseModel):
    connected: bool
    method: Literal["account", "api_key"] | None
    display_name: str | None
    expires_at: int | None
    pending: PendingRead | None


class AttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    attempt_id: str


class AccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    account_name: str
    password: SecretStr


class GuardRequest(AttemptRequest):
    code: SecretStr
    # Steam's EmailCode (2) and DeviceCode (3) confirmation types.
    code_type: Literal[2, 3]


@router.get("", response_model=ConnectionRead)
async def get_connection(current_user: User = Depends(get_current_user)) -> dict:
    return steam_connection.connection_status(current_user)


@router.post("/login/qr", response_model=ConnectionRead)
async def begin_qr(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict:
    return await steam_connection.begin(db, current_user.id)


@router.post("/login/account", response_model=ConnectionRead)
async def begin_account(
    body: AccountRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return await steam_connection.begin(
        db, current_user.id, body.account_name, body.password.get_secret_value()
    )


@router.post("/login/poll", response_model=ConnectionRead)
async def poll(
    body: AttemptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return await steam_connection.poll(db, current_user.id, body.attempt_id)


@router.post("/login/guard", response_model=ConnectionRead)
async def guard(
    body: GuardRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return await steam_connection.submit_guard(
        db, current_user.id, body.attempt_id, body.code.get_secret_value(), body.code_type
    )


@router.post("/login/cancel", response_model=ConnectionRead)
async def cancel(
    body: AttemptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return await steam_connection.cancel(db, current_user.id, body.attempt_id)


@router.delete("", response_model=ConnectionRead)
async def disconnect(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> dict:
    return await steam_connection.disconnect(db, current_user.id)


_OPENID_SESSION_KEY = "steam_openid"
_CONNECTIONS_PAGE = "/settings?area=account&section=connections"


@router.get("/openid/start")
async def openid_start(
    request: Request, current_user: User = Depends(get_current_user)
) -> RedirectResponse:
    """Send the person to Steam to prove which profile is theirs (identity only)."""
    return_to = f"{request.url_for('steam_openid_callback')}?state={secrets.token_urlsafe(24)}"
    # The signed session cookie binds this one attempt to the browser and the account.
    request.session[_OPENID_SESSION_KEY] = {
        "user": str(current_user.id),
        "return_to": return_to,
        "expires": int(time.time()) + steam_openid.ATTEMPT_SECONDS,
    }
    return RedirectResponse(
        steam_openid.login_url(return_to, str(request.base_url)), status_code=303
    )


@router.get("/openid/callback", name="steam_openid_callback")
async def openid_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RedirectResponse:
    """Verify Steam's answer once, then return to Connections with the outcome."""
    pending = request.session.pop(_OPENID_SESSION_KEY, None)
    outcome = "verified"
    try:
        if (
            not isinstance(pending, dict)
            or pending.get("user") != str(current_user.id)
            or not isinstance(pending.get("return_to"), str)
            or not isinstance(pending.get("expires"), int)
            or pending["expires"] < time.time()
        ):
            raise steam_openid.SteamOpenIdExpired("This Steam sign-in expired.")
        steam_id = await asyncio.to_thread(
            steam_openid.verify, dict(request.query_params), pending["return_to"]
        )
        await steam_connection.link_verified_identity(db, current_user.id, steam_id)
    except steam_openid.SteamOpenIdCancelled:
        outcome = "cancelled"
    except steam_openid.SteamOpenIdExpired:
        outcome = "expired"
    except steam_connection.SteamConnectionError:
        outcome = "conflict"
    except SteamLibraryError:
        outcome = "failed"
    return RedirectResponse(f"{_CONNECTIONS_PAGE}&steam_openid={outcome}", status_code=303)
