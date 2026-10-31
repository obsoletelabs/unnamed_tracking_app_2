"""Keep public plugin applications off the host's authenticated browser origin."""

from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.core.config import settings


def hostname(value: str) -> str:
    """Compare hostnames, not ports: browser cookies are not isolated by port."""
    try:
        return (urlsplit(value).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


class PublicAppOriginMiddleware:
    """A dedicated public-app hostname exposes only participant-token APIs.

    This is enforced behind the reverse proxy too. Public pages cannot read a
    host session even if a deployment accidentally forwards unrelated API paths
    or a browser carries an old/domain-wide cookie to the public hostname.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        public_host = hostname(settings.PUBLIC_APPS_ORIGIN)
        if not public_host:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        host = hostname("//" + headers.get(b"host", b"").decode("latin-1"))
        origin = hostname(headers.get(b"origin", b"").decode("latin-1"))
        public_path = scope.get("path", "").startswith("/api/public-apps/")
        if (host == public_host and not public_path) or (origin == public_host and not public_path):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse(
                    {"detail": "This origin is restricted to public-app routes."},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
            return
        if host != public_host:
            await self.app(scope, receive, send)
            return
        # Public app sessions use explicit bearer tokens, never host cookies.
        isolated = {
            **scope,
            "headers": [(key, value) for key, value in scope["headers"] if key != b"cookie"],
        }

        async def without_cookies(message):
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() != b"set-cookie"
                    ],
                }
            await send(message)

        await self.app(isolated, receive, without_cookies)
