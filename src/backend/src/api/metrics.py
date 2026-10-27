"""Request instrumentation and the guarded Prometheus scrape endpoint.

`/metrics` is answered here, before routing, so it is not an API route: it is absent from the
OpenAPI schema, cannot be shadowed by a plugin route and is never reachable as `/api/metrics`.
The production reverse proxy forwards only `/api/` and static assets to the backend, so the
endpoint is not part of the public listener; see wiki/docs/deployment/metrics.md.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core import metrics

METRICS_PATH = "/metrics"


class ScrapeAccessLogFilter(logging.Filter):  # pylint: disable=too-few-public-methods
    """Keep constant scraping out of the server's access log (and so out of the console).

    uvicorn passes (client, method, path-with-query, http_version, status) as the record's
    arguments; a scrape every few seconds would otherwise bury every other log line."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            return args[2].split("?", 1)[0] != METRICS_PATH
        return True


logging.getLogger("uvicorn.access").addFilter(ScrapeAccessLogFilter())


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

        observation = _HttpObservation(scope, time.perf_counter())
        metrics.HTTP_IN_FLIGHT.inc()
        try:
            await self.app(
                scope,
                observation.receive_wrapper(receive),
                observation.send_wrapper(send),
            )
        except asyncio.CancelledError:
            observation.disconnected("cancelled")
            raise
        except OSError:
            # A send failure has already been classified as a disconnect;
            # an OSError raised by application code is an application failure.
            if observation.response.disconnect_phase is None:
                observation.failed()
                metrics.HTTP_EXCEPTIONS.labels(metrics.route_label(scope)).inc()
            raise
        except Exception:
            observation.failed()
            metrics.HTTP_EXCEPTIONS.labels(metrics.route_label(scope)).inc()
            raise
        finally:
            observation.finish()
            metrics.HTTP_IN_FLIGHT.dec()


_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


@dataclass
class _ResponseProgress:
    status: int = 0
    headers_sent: bool = False
    expects_trailers: bool = False
    kind: str = "response"
    outcome: str = "incomplete"
    ended: float | None = None
    disconnect_phase: str | None = None


@dataclass
class _HttpObservation:
    scope: Scope
    started: float
    observed_start: bool = False
    response: _ResponseProgress = field(default_factory=_ResponseProgress)
    request_bytes: int = 0
    response_bytes: int = 0

    def observe_start(self) -> None:
        """Retain the existing time-to-response-start series and request counter."""
        if self.observed_start:
            return
        self.observed_start = True
        method = self.scope["method"] if self.scope["method"] in _METHODS else "OTHER"
        route = metrics.route_label(self.scope)
        status_class = f"{self.response.status // 100}xx" if self.response.status else "aborted"
        metrics.HTTP_REQUESTS.labels(method, route, status_class).inc()
        if self.response.status:
            metrics.HTTP_DURATION.labels(method, route).observe(time.perf_counter() - self.started)
        if self.response.status == 429:
            metrics.HTTP_RATE_LIMITED.labels(route).inc()

    def disconnected(self, outcome: str = "disconnected") -> None:
        """Latch the first terminal signal; do not reclassify a completed response."""
        if self.response.ended is None:
            self.response.ended = time.perf_counter()
            self.response.outcome = outcome
            self.response.disconnect_phase = (
                "body" if self.response.headers_sent else "before_headers"
            )

    def failed(self) -> None:
        """Record failures before or during the body, excluding background-task time."""
        if self.response.ended is None:
            self.response.ended = time.perf_counter()
            self.response.outcome = "error"
        if not self.response.status:
            self.response.status = 500

    def receive_wrapper(self, receive: Receive) -> Receive:
        """Observe only bytes the application chooses to consume."""

        async def instrumented_receive() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                self.request_bytes += len(message.get("body", b""))
            elif message["type"] == "http.disconnect":
                self.disconnected()
            return message

        return instrumented_receive

    def send_wrapper(self, send: Send) -> Send:
        """Keep the application protocol and backpressure intact."""

        async def instrumented_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.response.status = message["status"]
                self.response.expects_trailers = bool(message.get("trailers", False))
                if any(
                    key.lower() == b"content-type"
                    and value.lower().split(b";", 1)[0].strip() == b"text/event-stream"
                    for key, value in message.get("headers", [])
                ):
                    self.response.kind = "event_stream"
                self.observe_start()
            elif message["type"] == "http.response.body" and message.get("more_body", False):
                if self.response.kind == "response":
                    self.response.kind = "stream"
            elif message["type"] == "http.response.pathsend":
                self.response.kind = "file"
            try:
                await send(message)
            except OSError:
                self.disconnected()
                raise
            if message["type"] == "http.response.start":
                self.response.headers_sent = True
            elif message["type"] == "http.response.body":
                self.response_bytes += len(message.get("body", b""))
                if (
                    not message.get("more_body", False)
                    and not self.response.expects_trailers
                    and self.response.ended is None
                ):
                    self.response.ended = time.perf_counter()
                    self.response.outcome = "complete"
            elif (
                message["type"] == "http.response.trailers"
                and not message.get("more_trailers", False)
                and self.response.ended is None
            ):
                self.response.ended = time.perf_counter()
                self.response.outcome = "complete"
            elif message["type"] == "http.response.pathsend" and self.response.ended is None:
                self.response.ended = time.perf_counter()
                self.response.outcome = "complete"

        return instrumented_send

    def finish(self) -> None:
        """Emit once after routing has established its bounded route template."""
        self.observe_start()
        route = metrics.route_label(self.scope)
        method = self.scope["method"] if self.scope["method"] in _METHODS else "OTHER"
        metrics.HTTP_RESPONSE_DURATION.labels(
            method, route, self.response.kind, self.response.outcome
        ).observe(
            (self.response.ended if self.response.ended is not None else time.perf_counter())
            - self.started
        )
        metrics.HTTP_REQUEST_BYTES.labels(method, route).inc(self.request_bytes)
        # The sendfile extension exposes a filename, not observed body bytes.
        if self.response.kind != "file":
            metrics.HTTP_RESPONSE_BYTES.labels(method, route, self.response.kind).inc(
                self.response_bytes
            )
        if self.response.disconnect_phase is not None:
            metrics.HTTP_DISCONNECTS.labels(method, route, self.response.disconnect_phase).inc()
