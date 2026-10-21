"""Request instrumentation and the guarded Prometheus scrape endpoint.

`/metrics` is answered here, before routing, so it is not an API route: it is absent from the
OpenAPI schema, cannot be shadowed by a plugin route and is never reachable as `/api/metrics`.
The production reverse proxy forwards only `/api/` and static assets to the backend, so the
endpoint is not part of the public listener; see wiki/docs/deployment/metrics.md.
"""

import time

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core import metrics

METRICS_PATH = "/metrics"


def _bearer(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token.strip() if scheme.lower() == "bearer" else None
    return None


async def _scrape(scope: Scope, receive: Receive, send: Send) -> None:
    headers = {"Cache-Control": "no-store"}
    if not metrics.metrics_enabled():
        response: Response = JSONResponse({"detail": "Not Found"}, 404, headers)
    elif scope["method"] not in {"GET", "HEAD"}:
        response = JSONResponse({"detail": "Method Not Allowed"}, 405, {**headers, "Allow": "GET"})
    elif not metrics.token_valid(_bearer(scope)):
        response = JSONResponse(
            {"detail": "Metrics credentials are required."},
            401,
            {**headers, "WWW-Authenticate": "Bearer"},
        )
    else:
        response = Response(
            await metrics.render() if scope["method"] == "GET" else b"",
            media_type=metrics.CONTENT_TYPE,
            headers=headers,
        )
    await response(scope, receive, send)


class MetricsMiddleware:  # pylint: disable=too-few-public-methods
    """Count and time HTTP requests by route template; never by raw URL."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"].removeprefix(scope.get("root_path", "")) == METRICS_PATH:
            await _scrape(scope, receive, send)
            return

        started = time.perf_counter()
        status = 0
        observed = False

        def observe() -> None:
            nonlocal observed
            if observed:
                return
            observed = True
            route = metrics.route_label(scope)
            method = scope["method"] if scope["method"] in _METHODS else "OTHER"
            metrics.HTTP_REQUESTS.labels(method, route, f"{status // 100}xx").inc()
            metrics.HTTP_DURATION.labels(method, route).observe(time.perf_counter() - started)
            if status == 429:
                metrics.HTTP_RATE_LIMITED.labels(route).inc()

        async def instrumented_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                observe()
            await send(message)

        metrics.HTTP_IN_FLIGHT.inc()
        try:
            await self.app(scope, receive, instrumented_send)
        except Exception:
            if not observed:
                status = 500
                observe()
            metrics.HTTP_EXCEPTIONS.labels(metrics.route_label(scope)).inc()
            raise
        finally:
            metrics.HTTP_IN_FLIGHT.dec()


_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
