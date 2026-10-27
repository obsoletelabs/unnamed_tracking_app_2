"""Exercise ASGI response telemetry without buffering bodies or running providers."""

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src.api import metrics as middleware
from src.core import metrics

ROUTE = "/telemetry/{item}"
LABELS = {"method": "GET", "route": ROUTE}


def sample(name, **labels):
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


def scope():
    return {
        "type": "http",
        "method": "GET",
        "path": "/telemetry/private-title",
        "headers": [],
        "route": SimpleNamespace(path=ROUTE),
    }


async def no_receive():
    raise AssertionError("Instrumentation must not consume a request body itself")


async def accept(_message):
    return None


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(middleware, "time", SimpleNamespace(perf_counter=lambda: now[0]))
    return now


async def test_body_completion_is_separate_from_headers_and_background_work(clock):
    request_labels = {**LABELS, "status_class": "2xx"}
    body_labels = {**LABELS, "response_kind": "response"}
    terminal_labels = {**body_labels, "outcome": "complete"}
    before = {
        "start": sample("uta_http_request_duration_seconds_sum", **LABELS),
        "body": sample("uta_http_response_duration_seconds_sum", **terminal_labels),
        "requests": sample("uta_http_requests_total", **request_labels),
        "read": sample("uta_http_request_bytes_total", **LABELS),
        "sent": sample("uta_http_response_bytes_total", **body_labels),
        "flight": sample("uta_http_requests_in_flight"),
    }
    incoming = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"defg", "more_body": False},
        ]
    )

    async def receive():
        return next(incoming)

    async def app(_scope, receive, send):
        assert (await receive())["body"] == b"abc"
        assert (await receive())["body"] == b"defg"
        clock[0] = 2
        await send({"type": "http.response.start", "status": 200, "headers": []})
        clock[0] = 9
        await send({"type": "http.response.body", "body": b"hello"})
        clock[0] = 20  # A background task must not inflate response duration.

    await middleware.MetricsMiddleware(app)(scope(), receive, accept)
    assert sample("uta_http_request_duration_seconds_sum", **LABELS) == before["start"] + 2
    assert sample("uta_http_response_duration_seconds_sum", **terminal_labels) == before["body"] + 9
    assert sample("uta_http_requests_total", **request_labels) == before["requests"] + 1
    assert sample("uta_http_request_bytes_total", **LABELS) == before["read"] + 7
    assert sample("uta_http_response_bytes_total", **body_labels) == before["sent"] + 5
    assert sample("uta_http_requests_in_flight") == before["flight"]


@pytest.mark.parametrize("event_stream", [False, True])
async def test_streams_have_separate_kinds_and_count_only_body_bytes(clock, event_stream):
    kind = "event_stream" if event_stream else "stream"
    labels = {**LABELS, "response_kind": kind}
    before = sample("uta_http_response_bytes_total", **labels)
    count = sample("uta_http_response_duration_seconds_count", **labels, outcome="complete")

    async def app(_scope, _receive, send):
        headers = [(b"content-type", b"text/event-stream; charset=utf-8")] if event_stream else []
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        for value in (b"first", b"second"):
            clock[0] += 3
            await send({"type": "http.response.body", "body": value, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    await middleware.MetricsMiddleware(app)(scope(), no_receive, accept)
    assert sample("uta_http_response_bytes_total", **labels) == before + 11
    assert (
        sample("uta_http_response_duration_seconds_count", **labels, outcome="complete")
        == count + 1
    )


@pytest.mark.parametrize("after_headers", [False, True])
async def test_application_exceptions_record_one_terminal_error(clock, after_headers):
    labels = {**LABELS, "response_kind": "response", "outcome": "error"}
    before = sample("uta_http_response_duration_seconds_count", **labels)
    errors = sample("uta_http_exceptions_total", route=ROUTE)

    async def app(_scope, _receive, send):
        if after_headers:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        clock[0] = 4
        raise RuntimeError("A private exception must never be a label")

    with pytest.raises(RuntimeError):
        await middleware.MetricsMiddleware(app)(scope(), no_receive, accept)
    assert sample("uta_http_response_duration_seconds_count", **labels) == before + 1
    assert sample("uta_http_exceptions_total", route=ROUTE) == errors + 1


@pytest.mark.parametrize("after_headers", [False, True])
@pytest.mark.parametrize("signal", ["receive", "cancel", "send"])
async def test_disconnect_and_cancellation_are_counted_once(clock, after_headers, signal):
    phase = "body" if after_headers else "before_headers"
    outcome = "cancelled" if signal == "cancel" else "disconnected"
    kind = "stream" if after_headers and signal == "send" else "response"
    terminal = {**LABELS, "response_kind": kind, "outcome": outcome}
    before = sample("uta_http_response_duration_seconds_count", **terminal)
    disconnects = sample("uta_http_disconnects_total", **LABELS, phase=phase)
    errors = sample("uta_http_exceptions_total", route=ROUTE)
    flight = sample("uta_http_requests_in_flight")
    sent = sample("uta_http_response_bytes_total", **LABELS, response_kind=kind)

    async def send(message):
        if signal == "send" and (message["type"] == "http.response.body" or not after_headers):
            raise OSError("connection closed")

    async def receive():
        return {"type": "http.disconnect"}

    async def app(_scope, receive, send):
        if signal == "send" or after_headers:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        clock[0] = 5
        if signal == "receive":
            await receive()
            await receive()  # Duplicate disconnect signals must not count twice.
        elif signal == "send":
            await send({"type": "http.response.body", "body": b"not accepted", "more_body": True})
        raise asyncio.CancelledError

    with pytest.raises((asyncio.CancelledError, OSError)):
        await middleware.MetricsMiddleware(app)(scope(), receive, send)
    assert sample("uta_http_response_duration_seconds_count", **terminal) == before + 1
    assert sample("uta_http_disconnects_total", **LABELS, phase=phase) == disconnects + 1
    assert sample("uta_http_exceptions_total", route=ROUTE) == errors
    assert sample("uta_http_requests_in_flight") == flight
    assert sample("uta_http_response_bytes_total", **LABELS, response_kind=kind) == sent


async def test_trailers_complete_the_response_and_headers_are_not_body_bytes(clock):
    labels = {**LABELS, "response_kind": "response", "outcome": "complete"}
    before = sample("uta_http_response_duration_seconds_sum", **labels)

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [], "trailers": True})
        clock[0] = 2
        await send({"type": "http.response.body", "body": b"done"})
        clock[0] = 7
        await send({"type": "http.response.trailers", "headers": [(b"checksum", b"abc")]})

    await middleware.MetricsMiddleware(app)(scope(), no_receive, accept)
    assert sample("uta_http_response_duration_seconds_sum", **labels) == before + 7


async def test_incomplete_app_and_non_http_passthrough(clock):
    labels = {**LABELS, "response_kind": "response", "outcome": "incomplete"}
    before = sample("uta_http_response_duration_seconds_count", **labels)

    async def app(_scope, _receive, _send):
        clock[0] = 3

    wrapped = middleware.MetricsMiddleware(app)
    await wrapped(scope(), no_receive, accept)
    assert sample("uta_http_response_duration_seconds_count", **labels) == before + 1
    await wrapped({"type": "websocket"}, no_receive, accept)
    assert sample("uta_http_response_duration_seconds_count", **labels) == before + 1


def test_fresh_process_has_no_inherited_response_observations():
    environment = os.environ.copy()
    environment.pop("PROMETHEUS_MULTIPROC_DIR", None)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.core import metrics; "
            "assert metrics.REGISTRY.get_sample_value("
            "'uta_http_response_duration_seconds_count', "
            "{'method':'GET','route':'/telemetry/{item}',"
            "'response_kind':'response','outcome':'complete'}) is None",
        ],
        env=environment,
        check=True,
        timeout=15,
    )
