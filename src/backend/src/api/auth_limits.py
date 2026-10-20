"""HTTP adapters for shared authentication quotas and safe retry responses."""

from fastapi import Depends, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.api.auth_policy import ACTOR_LIMITS, authentication_operation, caller_limit
from src.core.auth import get_current_admin, get_current_user
from src.core.client_address import request_ip
from src.core.password_work import PasswordWorkBusy
from src.database.models.user import User
from src.features.auth_rate_limits import AuthLimit
from src.features.auth_throttle import (
    AuthOperation,
    AuthRateLimited,
    AuthStoreUnavailable,
    caller_network,
    record_auth_event,
    reserve_auth_attempt,
)


def auth_retry_response(
    error: AuthRateLimited | AuthStoreUnavailable | PasswordWorkBusy,
    operation: AuthOperation | None = None,
) -> JSONResponse:
    if isinstance(error, AuthRateLimited):
        status, retry, message = 429, error.retry_after, str(error)
    elif isinstance(error, PasswordWorkBusy):
        status, retry, message = 503, 1, "Authentication is busy. Try again shortly."
        record_auth_event(operation or AuthOperation.LOGIN_ACCOUNT, "busy")
    else:
        status, retry, message = (
            503,
            5,
            "Authentication protection is temporarily unavailable. Try again shortly.",
        )
    return JSONResponse(
        {"detail": message},
        status_code=status,
        headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
    )


class AuthThrottleMiddleware:  # pylint: disable=too-few-public-methods
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        operation = (
            authentication_operation(scope["method"], scope["path"], scope.get("root_path", ""))
            if scope["type"] == "http"
            else None
        )
        if operation is not None:
            ceiling, interval = caller_limit(operation)
            caller = caller_network(request_ip(Request(scope)))
            try:
                await reserve_auth_attempt(
                    operation, (AuthLimit("request-caller", caller, ceiling, interval),)
                )
            except (AuthRateLimited, AuthStoreUnavailable) as error:
                await auth_retry_response(error, operation)(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def enforce_actor_quota(request: Request, user: User) -> None:
    operation = authentication_operation(
        request.method, request.url.path, request.scope.get("root_path", "")
    )
    if operation is not None and operation in ACTOR_LIMITS:
        actor = getattr(request.state, "authenticated_actor", None)
        if (
            operation == AuthOperation.API_KEY_CREATE
            and actor is not None
            and actor.credential_kind != "session"
        ):
            return  # The route rejects this provenance before issuing a key.
        ceiling, interval = ACTOR_LIMITS[operation]
        await reserve_auth_attempt(
            operation, (AuthLimit("actor", str(user.id), ceiling, interval),)
        )


async def enforce_auth_actor_quota(
    request: Request, user: User = Depends(get_current_user)
) -> None:
    await enforce_actor_quota(request, user)


async def enforce_admin_auth_quota(
    request: Request, admin: User = Depends(get_current_admin)
) -> None:
    await enforce_actor_quota(request, admin)
