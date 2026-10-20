"""Authentication IPs come from the server's validated peer, never a caller's header."""

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.core.session_manager import request_ip


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        ("203.0.113.10", "203.0.113.10"),
        ("2001:db8:0:0::1", "2001:db8::1"),
        ("::ffff:192.0.2.2", "192.0.2.2"),
        ("fe80::1%eth0", "fe80::1"),
        ("127.0.0.1", "127.0.0.1"),
        ("unknown", None),
        (None, None),
    ],
)
def test_headers_cannot_replace_the_effective_peer(peer, expected):
    request = Request(
        {
            "type": "http",
            "client": (peer, 1234) if peer is not None else None,
            "headers": [
                (b"x-real-ip", b"198.51.100.99"),
                (b"x-forwarded-for", b"198.51.100.98"),
                (b"forwarded", b"for=198.51.100.97"),
            ],
        }
    )
    assert request_ip(request) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("203.0.113.10", "198.51.100.20", "203.0.113.10"),
        ("127.0.0.1", "198.51.100.20", "198.51.100.20"),
        ("127.0.0.1", "unknown", None),
        ("203.0.113.10", "unknown", "203.0.113.10"),
        ("127.0.0.1", "2001:db8::1", "2001:db8::1"),
        ("127.0.0.1", "198.51.100.20, 127.0.0.1", "198.51.100.20"),
        ("203.0.113.10", "198.51.100.20, 127.0.0.1", "203.0.113.10"),
    ],
)
async def test_only_the_servers_trusted_proxy_boundary_can_update_peer(peer, forwarded, expected):
    async def echo(scope, receive, send):
        response = JSONResponse({"ip": request_ip(Request(scope))})
        await response(scope, receive, send)

    application = ProxyHeadersMiddleware(echo, trusted_hosts=["127.0.0.1"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, client=(peer, 1234)),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/", headers={"X-Forwarded-For": forwarded, "X-Real-IP": "198.51.100.99"}
        )
    assert response.json() == {"ip": expected}
