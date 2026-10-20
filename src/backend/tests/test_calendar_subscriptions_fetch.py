"""Exercise the real public-address/pinning helper with an in-memory HTTP socket boundary."""

import asyncio
import io
import socket
from types import SimpleNamespace

import pytest

from src.features import calendar_fetch
from src.features.calendar_fetch import CalendarFetchError, fetch_calendar
from src.features.calendar_ics import MAX_BYTES, CalendarParseError
from src.helpers import public_http
from tests.test_calendar_subscriptions_parser import feed


class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self.body = io.BytesIO(
            body if body is not None else feed("UID:a\r\nDTSTART:20261019T120000Z")
        )
        self.headers = headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, size):
        return self.body.read(size)

    def read1(self, size):
        return self.read(size)


@pytest.fixture(name="network")
def network_fixture(monkeypatch):
    state = SimpleNamespace(
        responses=[Response()], dns=[], connected=[], requests=[], addresses={}, header_wait=None
    )

    def resolve(host, port, **_kwargs):
        state.dns.append(host)
        addresses = state.addresses.get(host, ["93.184.216.34"])
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port),
            )
            for address in addresses
        ]

    class Socket:
        def shutdown(self, _how):
            pass

        def settimeout(self, _value):
            pass

        def connect(self, address):
            state.connected.append(address)

        def close(self):
            pass

    class Connection:
        def __init__(self, host, port, **_kwargs):
            self.host, self.port, self.sock = host, port, None

        def request(self, method, target, headers):
            self.sock = self._create_connection(None)
            state.requests.append((self.host, method, target, dict(headers)))

        def getresponse(self):
            if state.header_wait:
                state.header_wait()
            return state.responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(public_http.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(public_http.socket, "socket", lambda *_args: Socket())
    monkeypatch.setattr(public_http, "HTTPSConnection", Connection)
    return state


def test_public_connection_pins_one_resolution_retains_host_and_ignores_proxy_env(
    network, monkeypatch
):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1234")
    network.responses = [
        Response(headers={"ETag": '"v1"', "Last-Modified": "Mon, 19 Oct 2026 00:00:00 GMT"})
    ]
    result = fetch_calendar("https://calendar.example/private-token.ics")
    assert len(result.components) == 1 and result.etag == '"v1"'
    assert network.dns == ["calendar.example"]
    assert network.connected == [("93.184.216.34", 443)]
    assert network.requests[0][:3] == ("calendar.example", "GET", "/private-token.ics")


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["10.0.0.1"],
        ["100.64.0.1"],
        ["::1"],
        ["::ffff:127.0.0.1"],
        ["224.0.0.1"],
        ["93.184.216.34", "192.168.0.1"],
    ],
)
def test_whole_dns_answer_is_blocked_before_connection(network, addresses):
    network.addresses["calendar.example"] = addresses
    with pytest.raises(CalendarFetchError, match="blocked_address"):
        fetch_calendar("https://calendar.example/token")
    assert not network.connected and not network.requests


@pytest.mark.parametrize(
    "location",
    [
        "http://other.example/a",
        "https://user:pass@other.example/a",
        "file:///etc/passwd",
        "https://private.example/a",
        "https://other.example:8443/a",
    ],
)
def test_every_redirect_is_revalidated_without_forwarding_validators(network, location):
    network.addresses["private.example"] = ["127.0.0.1"]
    network.responses = [Response(302, headers={"Location": location})]
    with pytest.raises(CalendarFetchError):
        fetch_calendar("https://calendar.example/token", '"private-etag"')
    assert len(network.requests) == 1


def test_redirect_limit_and_conditional_304(network):
    network.responses = [
        Response(302, headers={"Location": "https://other.example/feed"}),
        Response(200),
    ]
    fetch_calendar("https://calendar.example/token", '"private-etag"')
    assert "If-None-Match" not in network.requests[-1][3]
    network.responses = [Response(302, headers={"Location": "/again"}) for _ in range(4)]
    with pytest.raises(CalendarFetchError, match="redirect"):
        fetch_calendar("https://calendar.example/token")
    network.responses = [Response(304)]
    assert fetch_calendar("https://calendar.example/token", '"v1"').status == "not_modified"
    network.responses = [Response(304)]
    with pytest.raises(CalendarFetchError, match="http_error"):
        fetch_calendar("https://calendar.example/token")


@pytest.mark.parametrize(
    "response,code",
    [
        (Response(401), "authentication"),
        (Response(403), "authentication"),
        (Response(500), "http_error"),
        (Response(headers={"Content-Encoding": "gzip"}), "encoding"),
        (Response(headers={"Content-Length": str(MAX_BYTES + 1)}), "too_large"),
        (Response(body=b"x" * (MAX_BYTES + 1)), "too_large"),
        (Response(body=b"short", headers={"Content-Length": "999"}), "truncated"),
        (Response(body=b"partial"), "malformed"),
    ],
)
def test_error_bodies_never_replace_a_snapshot_or_leak_secret_url(network, response, code):
    network.responses = [response]
    with pytest.raises((CalendarFetchError, CalendarParseError), match=code) as failure:
        fetch_calendar("https://calendar.example/secret-token")
    assert "secret-token" not in str(failure.value)


async def test_cancelled_request_does_not_release_its_running_worker_slot(monkeypatch):
    import threading

    started, finish = threading.Event(), threading.Event()
    slots = asyncio.Semaphore(1)
    monkeypatch.setattr(calendar_fetch, "_SLOTS", slots)

    def blocked():
        started.set()
        finish.wait(5)
        return 1

    task = asyncio.create_task(calendar_fetch.calendar_work(blocked))
    while not started.is_set():
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert slots.locked()
    finish.set()
    for _ in range(100):
        if not slots.locked():
            break
        await asyncio.sleep(0.01)
    assert not slots.locked()


async def test_global_fetch_slots_are_enforced_across_independent_database_sessions(monkeypatch):
    from sqlalchemy import text

    from src.database.session import SessionLocal

    calls = []
    monkeypatch.setattr(calendar_fetch, "fetch_calendar", lambda *_args: calls.append(True))
    async with SessionLocal() as first, SessionLocal() as second:
        assert await first.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": calendar_fetch.FETCH_LOCKS[0]}
        )
        assert await second.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": calendar_fetch.FETCH_LOCKS[1]}
        )
        with pytest.raises(CalendarFetchError, match="busy"):
            await calendar_fetch.refresh_snapshot("https://calendar.example/token", None, None)
        assert calls == []
        await first.rollback()
        await calendar_fetch.refresh_snapshot("https://calendar.example/token", None, None)
        assert calls == [True]


@pytest.mark.parametrize("stage", ["headers", "body"])
def test_slow_buffered_reads_are_aborted_at_the_total_socket_deadline(network, monkeypatch, stage):
    import threading
    import time

    stopped = threading.Event()
    monkeypatch.setattr(calendar_fetch, "TRANSPORT_SECONDS", 0.1)
    monkeypatch.setattr(public_http.ssl, "create_default_context", lambda: object())

    class Socket:
        def settimeout(self, _value):
            pass

        def connect(self, _address):
            pass

        def close(self):
            pass

        def shutdown(self, _how):
            stopped.set()

    class SlowResponse(Response):
        def read1(self, _size):
            blocked_read()

    def blocked_read():
        assert stopped.wait(2), "The deadline did not interrupt the blocked read"
        raise TimeoutError

    monkeypatch.setattr(public_http.socket, "socket", lambda *_args: Socket())
    network.responses = [SlowResponse()]
    if stage == "headers":
        network.header_wait = blocked_read
    before = time.monotonic()
    with pytest.raises(CalendarFetchError, match="timeout"):
        fetch_calendar("https://calendar.example/secret-token")
    assert stopped.is_set() and time.monotonic() - before < 1
