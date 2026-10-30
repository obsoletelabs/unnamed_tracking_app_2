"""A metrics-only ASGI listener sharing the backend's event loop and registry."""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from src.api.metrics import METRICS_PATH, _scrape


class MetricsOnlyApp:  # pylint: disable=too-few-public-methods
    """The direct scrape port never delegates to the application router."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        if scope["path"] != METRICS_PATH:
            await JSONResponse({"detail": "Not Found"}, 404, {"Cache-Control": "no-store"})(
                scope, receive, send
            )
            return
        await _scrape(scope, receive, send, require_token=False)


class _NoSignalServer(uvicorn.Server):
    """The primary API server owns process signals."""

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class MetricsListener:
    """Bind before startup completes, then serve on the current asyncio loop."""

    def __init__(self, host: str, port: int) -> None:
        if not 1 <= port <= 65535 or port == 8000:
            raise ValueError("METRICS_LISTEN_PORT must be 1-65535 and differ from API port 8000")
        self.host = host
        self.port = port
        self.socket: socket.socket | None = None
        self.server: _NoSignalServer | None = None
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.task is not None:
            raise RuntimeError("Metrics listener is already running")
        worker_count = os.getenv("WEB_CONCURRENCY", "1")
        if not worker_count.isdecimal() or int(worker_count) != 1:
            raise RuntimeError("The direct metrics listener requires one backend worker")
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        bound = socket.socket(family, socket.SOCK_STREAM)
        try:
            bound.bind((self.host, self.port))
        except OSError as exc:
            bound.close()
            raise RuntimeError(
                f"Cannot bind metrics listener on {self.host}:{self.port}; "
                "check METRICS_LISTEN_PORT and port conflicts"
            ) from exc
        self.socket = bound
        config = uvicorn.Config(
            MetricsOnlyApp(),
            host=self.host,
            port=self.port,
            lifespan="off",
            # Metrics are plain HTTP; this also skips uvicorn's deprecated websockets import.
            ws="none",
            access_log=False,
            proxy_headers=False,
            log_level="warning",
        )
        self.server = _NoSignalServer(config)
        self.task = asyncio.create_task(self.server.serve(sockets=[bound]))
        try:
            async with asyncio.timeout(5):
                while not self.server.started:
                    if self.task.done():
                        await self.task
                        raise RuntimeError("Metrics listener stopped during startup")
                    await asyncio.sleep(0.01)
        except (Exception, asyncio.CancelledError):
            await self.stop()
            raise

    async def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            try:
                async with asyncio.timeout(5):
                    await self.task
            except TimeoutError:
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
        if self.socket is not None:
            self.socket.close()
        self.task = None
        self.socket = None
        self.server = None
