"""Large request bodies are accepted only from callers with verified credentials.

FastAPI reads or spools a whole body before a route's authentication runs, so
without this an anonymous client could make the server store any amount of
data. Bodies up to ``ANONYMOUS_BODY_LIMIT`` (logins, setup, public-app and
plugin routes, which cap their own bodies far lower) pass straight through.
Larger or unsized bodies need a credential that the matching authentication
accepts, or they are discarded without being stored and answered with 413; each
route still applies its own authorization and upload limits.
"""

from fastapi import HTTPException, Request
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.core.auth import get_current_user
from src.database.session import SessionLocal
from src.features.delegation import TOKEN_PREFIX as DELEGATED_PREFIX
from src.features.delegation import authenticate_grant
from src.plugin_api.gateway import runtime_token_is_valid
from src.plugin_api.management_auth import MANAGEMENT_PREFIX, management_key

ANONYMOUS_BODY_LIMIT = 1024 * 1024


async def _has_verified_credentials(scope: Scope) -> bool:
    # A private state keeps this check from setting the route's authenticated actor.
    request = Request({**scope, "state": {}})
    if runtime_token_is_valid(request.headers.get("x-plugin-runtime-token")):
        return True
    authorization = request.headers.get("authorization", "")
    async with SessionLocal() as db:
        try:
            if authorization.startswith(f"Bearer {MANAGEMENT_PREFIX}"):
                await management_key(db, authorization)
            elif authorization.startswith(f"Bearer {DELEGATED_PREFIX}"):
                await authenticate_grant(db, authorization[7:].strip())
            else:
                await get_current_user(request, db)
        except HTTPException:
            return False
    return True


def _needs_credentials(headers: Headers) -> bool:
    length = headers.get("content-length")
    if length is not None:
        return not length.isdigit() or int(length) > ANONYMOUS_BODY_LIMIT
    # A chunked body has no declared size; a request without either has no body.
    return "transfer-encoding" in headers


# ASGI middleware is a callable with no other public methods.
class AnonymousBodyLimitMiddleware:  # pylint: disable=too-few-public-methods
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["path"].startswith("/api/")
            and _needs_credentials(Headers(scope=scope))
            and not await _has_verified_credentials(scope)
        ):
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
