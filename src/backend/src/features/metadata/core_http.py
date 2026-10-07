"""Cancellable HTTP and wire normalization for the app's hardcoded providers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .identity import normalized_title

context: ContextVar[tuple[dict[str, Any], dict[str, str]]] = ContextVar("core_metadata_context")
_locks: dict[str, asyncio.Lock] = {}
_next_at: dict[str, float] = {}
OPERATIONS = {"search": "search", "metadata": "metadata", "media": "media", "health": "health"}


class ProviderFailure(RuntimeError):
    """A safe classification, never a remote body or credential-bearing URL."""

    def __init__(self, code: str, retry_after: int | None = None, *, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
        self.status = status

    def response(self) -> dict[str, Any]:
        failure: dict[str, Any] = {"code": self.code}
        if self.retry_after is not None:
            failure["retry_after_seconds"] = self.retry_after
        return {"failure": failure}


def configuration(_provider_id: str) -> dict[str, str]:
    """The native adapter supplies this authenticated user's resolved configuration."""
    return context.get()[1]


def provider_values(provider_id: str, required: tuple[str, ...]) -> dict[str, str]:
    values = configuration(provider_id)
    if any(not values.get(key) for key in required):
        raise ProviderFailure("invalid_configuration" if values else "not_configured")
    return values


def _retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return min(86400, max(0, math.ceil(seconds))) if math.isfinite(seconds) else None


async def _network(url: str, **options: Any) -> Any:
    method = options.pop("method", "GET")
    response_format = options.pop("response_format", "json")
    headers = {"User-Agent": "unnamed-tracking-app/1.0", **options.pop("headers", {})}
    body = options.pop("body", None)
    text_body = options.pop("text_body", None)
    try:
        async with httpx.AsyncClient(timeout=7, follow_redirects=True, max_redirects=3) as client:
            async with client.stream(
                method, url, headers=headers, json=body, content=text_body, **options
            ) as response:
                if response.status_code in {400, 401, 403}:
                    raise ProviderFailure("invalid_configuration", status=response.status_code)
                if response.status_code == 429:
                    raise ProviderFailure(
                        "rate_limited",
                        _retry_after(response.headers.get("Retry-After")),
                        status=429,
                    )
                if response.status_code == 404:
                    return {}
                if not response.is_success:
                    raise ProviderFailure("unavailable", status=response.status_code)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 16 * 1024 * 1024:
                        raise ProviderFailure("invalid_response")
                if response_format == "text":
                    return content.decode("utf-8", errors="replace")
                return json.loads(content)
    except httpx.TimeoutException as exc:
        raise ProviderFailure("timeout") from exc
    except httpx.HTTPError as exc:
        raise ProviderFailure("unavailable") from exc


class ProviderHttp:
    """Share pacing by credential context without reserving future background slots."""

    # A callable transport has one public request operation.
    # pylint: disable=too-few-public-methods

    def __init__(self, work: dict[str, Any], namespace: str, *, min_gap: float = 0):
        self.background = work.get("policy") == "background"
        self.deadline = time.monotonic() + (24 if self.background else 7)
        values = context.get(({}, {}))[1]
        fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        self.key = namespace + ":" + fingerprint
        self.min_gap = min_gap

    async def _pace(self) -> None:
        wait_deadline = min(self.deadline, time.monotonic() + (24 if self.background else 1))
        lock = _locks.setdefault(self.key, asyncio.Lock())
        while time.monotonic() < wait_deadline:
            async with asyncio.timeout(max(0.001, wait_deadline - time.monotonic())):
                async with lock:
                    delay = max(0, _next_at.get(self.key, 0) - time.monotonic())
                    if delay == 0:
                        _next_at[self.key] = time.monotonic() + self.min_gap
                        return
            if time.monotonic() + delay >= wait_deadline:
                raise ProviderFailure("rate_limited", min(86400, math.ceil(delay)))
            await asyncio.sleep(delay)
        raise ProviderFailure("rate_limited", 1)

    async def __call__(self, url: str, **options: Any) -> Any:
        for attempt in range(3 if self.background else 1):
            await self._pace()
            try:
                async with asyncio.timeout(max(0.001, self.deadline - time.monotonic())):
                    return await _network(url, **options)
            except ProviderFailure as exc:
                if exc.code not in {"rate_limited", "unavailable"}:
                    raise
                delay = exc.retry_after if exc.retry_after is not None else 2**attempt
                if exc.code == "rate_limited":
                    _next_at[self.key] = max(_next_at.get(self.key, 0), time.monotonic() + delay)
                if not self.background or attempt == 2 or time.monotonic() + delay >= self.deadline:
                    raise
                await asyncio.sleep(delay)
        raise ProviderFailure("unavailable")


async def network(url: str, **options: Any) -> Any:
    work = context.get()[0]
    return await ProviderHttp(work, "steamgriddb", min_gap=0.15)(url, **options)


def external_id(candidate: dict[str, Any], provider_id: str, namespace: str) -> str | None:
    return candidate.get("provider_ids", {}).get(namespace) or (
        candidate.get("external_id") if candidate.get("provider") == provider_id else None
    )


def exact_identity(candidates: list[dict], selected: dict) -> dict | None:
    titles = {
        normalized_title(value)
        for value in [selected["title"], *selected.get("alternate_titles", [])]
    }
    matches = [
        item
        for item in candidates
        if normalized_title(item["title"]) in titles
        and (not selected.get("year") or item.get("year") == selected["year"])
    ]
    return matches[0] if len(matches) == 1 else None


def metadata_page(work: dict, key: str, entries: list[dict], base: dict | None = None) -> dict:
    """Page large collections using the same bounds and cursors as optional providers."""
    offset = int(work.get("cursor") or 0)
    if offset < 0:
        raise ProviderFailure("invalid_response")
    patch = {**(base or {}), key: []}
    response: dict[str, Any] = {"metadata": patch}
    for entry in entries[offset : offset + 200]:
        patch[key].append(entry)
        if len(json.dumps(response, ensure_ascii=False).encode()) > 48 * 1024:
            patch[key].pop()
            if not patch[key]:
                raise ProviderFailure("invalid_response")
            break
    next_offset = offset + len(patch[key])
    if next_offset < len(entries):
        response["next_cursor"] = str(next_offset)
    return response
