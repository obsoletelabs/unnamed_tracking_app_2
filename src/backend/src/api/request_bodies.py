"""Large request bodies are accepted only from callers with verified credentials.

FastAPI reads or spools a whole body before a route's authentication runs, so
without this an anonymous client could make the server store any amount of
data. Bodies up to ``ANONYMOUS_BODY_LIMIT`` (logins, setup, public-app and
plugin routes, which cap their own bodies far lower) pass straight through.
Larger or unsized bodies need a credential that the matching authentication
accepts, or they are discarded without being stored and answered with 413; each
route still applies its own authorization and upload limits.
"""

import asyncio

from fastapi import HTTPException, Request
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.api.auth_limits import auth_retry_response
from src.api.auth_policy import AUTH_REQUEST_MAX_BYTES, authentication_operation
from src.core.auth import get_current_user
from src.core.auth_checks import check_credential
from src.database.session import SessionLocal
from src.features.auth_throttle import AuthRateLimited, AuthStoreUnavailable
from src.features.delegation import TOKEN_PREFIX as DELEGATED_PREFIX
from src.features.delegation import authenticate_grant
from src.plugin_api.gateway import runtime_token_is_valid
from src.plugin_api.management_auth import MANAGEMENT_PREFIX, management_key

ANONYMOUS_BODY_LIMIT = 1024 * 1024
AUTH_BODY_DRAIN_TIMEOUT = 5


async def _has_verified_credentials(scope: Scope) -> bool:
    # A private state keeps this check from setting the route's authenticated actor.
    request = Request({**scope, "state": {}})
    if runtime_token_is_valid(request.headers.get("x-plugin-runtime-token")):
        return True
    authorization = request.headers.get("authorization", "")
    async with SessionLocal() as db:
        try:
            if authorization.startswith(f"Bearer {MANAGEMENT_PREFIX}"):
                await check_credential(request, lambda: management_key(db, authorization))
            elif authorization.startswith(f"Bearer {DELEGATED_PREFIX}"):
                await check_credential(
                    request, lambda: authenticate_grant(db, authorization[7:].strip())
                )
            else:
                await get_current_user(request, db)
        except HTTPException:
            return False
    return True


def _needs_credentials(headers: Headers) -> bool:
    length = headers.get("content-length")
    if length is not None:
        return not _valid_length(length) or int(length) > ANONYMOUS_BODY_LIMIT
    # A chunked body has no declared size; a request without either has no body.
    return "transfer-encoding" in headers


def _valid_length(value: str) -> bool:
    return len(value) <= 20 and value.isascii() and value.isdecimal()


def _auth_body_too_large(scope: Scope, headers: Headers) -> bool:
    if authentication_operation(scope["method"], scope["path"], scope.get("root_path", "")) is None:
        return False
    length = headers.get("content-length")
    if length is None:
        return "transfer-encoding" in headers
    return not _valid_length(length) or int(length) > AUTH_REQUEST_MAX_BYTES


async def _discard_auth_body(receive: Receive) -> bool:
    """Drain modest invalid requests for streaming proxies, with bounded bytes/time."""
    received = 0
    try:
        async with asyncio.timeout(AUTH_BODY_DRAIN_TIMEOUT):
            while True:
                message = await receive()
                received += len(message.get("body", b""))
                if not message.get("more_body", False):
                    return True
                if received >= ANONYMOUS_BODY_LIMIT:
                    return False
    except TimeoutError:
        return False


# ASGI middleware is a callable with no other public methods.
class AnonymousBodyLimitMiddleware:  # pylint: disable=too-few-public-methods
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _auth_body_too_large(scope, Headers(scope=scope)):
            drained = await _discard_auth_body(receive)
            response = JSONResponse(
                {"detail": "Authentication requests require a declared body of at most 32 KB."},
                status_code=413,
                headers={
                    "Cache-Control": "no-store",
                    **({} if drained else {"Connection": "close"}),
                },
            )
            await response(scope, receive, send)
            return
        needs_verification = (
            scope["type"] == "http"
            and scope["path"].startswith("/api/")
            and _needs_credentials(Headers(scope=scope))
        )
        try:
            rejected = needs_verification and not await _has_verified_credentials(scope)
        except (AuthRateLimited, AuthStoreUnavailable) as error:
            await auth_retry_response(error)(scope, receive, send)
            return
        if rejected:
            # Discard the body unstored: answering before it arrives makes a
            # streaming proxy report a broken upstream instead of this response.
            while (await receive()).get("more_body", False):
                pass
            response = JSONResponse(
                {"detail": "Sign in to send a request body larger than 1 MB."},
                status_code=413,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
