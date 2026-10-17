"""Private, actor-scoped Steam connection controls."""

from collections.abc import Callable, Coroutine
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import get_current_user
from src.database.models.user import User
from src.database.session import get_db
from src.features.imports import steam_connection
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
    # SteamID64 proven by Steam's OpenID redirect; identity only, not library access.
    verified_steam_id: str | None = None


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
