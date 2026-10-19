"""Bounded subscription transport using the host's pinned public HTTP boundary."""

from __future__ import annotations

import asyncio
import hashlib
import socket
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from http.client import HTTPException
from threading import Timer
from typing import Any, TypeVar
from urllib.parse import urljoin

from sqlalchemy import text

from src.database.session import SessionLocal
from src.features.calendar_ics import (
    MAX_BYTES,
    CalendarComponent,
    CalendarParseError,
    parse_calendar,
)
from src.helpers.public_http import parse_http_url, public_connection

_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="calendar-fetch")
_SLOTS = asyncio.Semaphore(2)
_T = TypeVar("_T")
FETCH_LOCKS = (68438261019001, 68438261019002)
_PENDING: set[asyncio.Task[Any]] = set()
TRANSPORT_SECONDS = 20


def _abort_socket(sock: socket.socket | None) -> None:
    """A slow drip must not keep a buffered header/body read alive past the deadline."""
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class CalendarFetchError(ValueError):
    """A safe status code, never the requested URL or response content."""


@dataclass
class CalendarSnapshot:
    status: str
    components: list[CalendarComponent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    digest: str | None = None


def calendar_url(value: str) -> tuple[str, str]:
    """Do not resolve on save; every fetch resolves and pins again, including redirects."""
    if value.lower().startswith("webcal://"):
        value = "https://" + value[9:]
    try:
        parts = parse_http_url(value, allowed_ports={443})
        if parts.scheme != "https" or len(str(parts.hostname)) > 253:
            raise ValueError
    except ValueError:
        raise CalendarFetchError("invalid_url") from None
    return value, str(parts.hostname)


def _header(value: str | None) -> str | None:
    return (
        value
        if value and len(value) <= 512 and not any(ord(c) < 32 or ord(c) == 127 for c in value)
        else None
    )


def fetch_calendar(
    url: str, etag: str | None = None, modified: str | None = None
) -> CalendarSnapshot:
    """Reject partial bodies and non-identity encoding before reconciling any snapshot."""
    # Each transport/redirect/body guard is kept at this boundary for a reviewable security sequence.
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    deadline = time.monotonic() + TRANSPORT_SECONDS
    original = calendar_url(url)[0]
    headers = {
        "Accept": "text/calendar",
        "Accept-Encoding": "identity",
        "User-Agent": "UTApp-Calendar/1",
    }
    if _header(etag):
        headers["If-None-Match"] = str(etag)
    if _header(modified):
        headers["If-Modified-Since"] = str(modified)
    try:
        for redirect in range(4):
            url, _hostname = calendar_url(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CalendarFetchError("timeout")
            connection = public_connection(
                url, timeout=remaining, connect_timeout=min(5, remaining)
            )
            cutoff = None
            try:
                if time.monotonic() >= deadline:
                    raise CalendarFetchError("timeout")
                parts = parse_http_url(url)
                target = parts.path or "/"
                if parts.query:
                    target += "?" + parts.query
                connection.request("GET", target, headers=headers)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CalendarFetchError("timeout")
                # HTTPResponse uses buffered readline/read calls. A per-recv timeout alone
                # permits an indefinite slow drip, so close the connected socket at the deadline.
                sock = connection.sock
                cutoff = Timer(remaining, _abort_socket, args=(sock,))
                cutoff.daemon = True
                cutoff.start()
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or redirect == 3:
                        raise CalendarFetchError("redirect")
                    url = calendar_url(urljoin(url, location))[0]
                    # Validators may reveal private identifiers to a different redirect target.
                    headers = {
                        k: v
                        for k, v in headers.items()
                        if k not in {"If-None-Match", "If-Modified-Since"}
                    }
                    continue
                if response.status == 304:
                    if url != original or not (etag or modified):
                        raise CalendarFetchError("http_error")
                    return CalendarSnapshot(
                        status="not_modified", etag=etag, last_modified=modified
                    )
                if response.status != 200:
                    raise CalendarFetchError(
                        "authentication" if response.status in {401, 403} else "http_error"
                    )
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise CalendarFetchError("encoding")
                length = response.getheader("Content-Length")
                if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
                    raise CalendarFetchError("too_large")
                body = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CalendarFetchError("timeout")
                    if sock:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(64 * 1024, MAX_BYTES + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise CalendarFetchError("too_large")
                if time.monotonic() >= deadline:
                    raise CalendarFetchError("timeout")
                if length is not None and len(body) != int(length):
                    raise CalendarFetchError("truncated")
                components, warnings = parse_calendar(bytes(body))
                return CalendarSnapshot(
                    "updated",
                    components,
                    warnings,
                    _header(response.getheader("ETag")),
                    _header(response.getheader("Last-Modified")),
                    hashlib.sha256(body).hexdigest(),
                )
            finally:
                if cutoff is not None:
                    cutoff.cancel()
                connection.close()
    except (CalendarFetchError, CalendarParseError):
        raise
    except TimeoutError:
        raise CalendarFetchError("timeout") from None
    except ValueError:
        raise CalendarFetchError("blocked_address") from None
    except (OSError, HTTPException):
        raise CalendarFetchError("unavailable") from None
    raise CalendarFetchError("redirect")


async def _worker(work: Callable[[], _T]) -> _T:
    """No unbounded executor queue; cancellation retains its slot until the worker exits."""
    try:
        await asyncio.wait_for(_SLOTS.acquire(), timeout=1)
    except TimeoutError:
        raise CalendarFetchError("busy") from None
    future = asyncio.get_running_loop().run_in_executor(_WORKERS, work)

    def completed(result: asyncio.Future[_T]) -> None:
        _SLOTS.release()
        if not result.cancelled():
            result.exception()  # Consume a late failure after request timeout/cancellation.

    future.add_done_callback(completed)
    return await asyncio.shield(future)


async def _bounded(work: Coroutine[Any, Any, _T]) -> _T:
    # Keep a strong reference after caller cancellation; resources live until the worker exits.
    task = asyncio.create_task(work)
    _PENDING.add(task)

    def completed(result: asyncio.Task[Any]) -> None:
        _PENDING.discard(result)
        if not result.cancelled():
            result.exception()

    task.add_done_callback(completed)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=25)
    except TimeoutError:
        raise CalendarFetchError("timeout") from None


async def calendar_work(work: Callable[[], _T]) -> _T:
    return await _bounded(_worker(work))


async def _network_snapshot(url: str, etag: str | None, modified: str | None) -> CalendarSnapshot:
    async with SessionLocal() as db:
        try:
            # Transaction-level advisory locks bound fetches across all workers/nodes on this database.
            # A pool/DNS stall must not create an unbounded queue of deferred fetches.
            async with asyncio.timeout(1):
                claimed = False
                for key in FETCH_LOCKS:
                    if await db.scalar(
                        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
                    ):
                        claimed = True
                        break
                if not claimed:
                    raise CalendarFetchError("busy")
        except TimeoutError:
            raise CalendarFetchError("busy") from None
        return await _worker(partial(fetch_calendar, url, etag, modified))


async def refresh_snapshot(url: str, etag: str | None, modified: str | None) -> CalendarSnapshot:
    return await _bounded(_network_snapshot(url, etag, modified))
