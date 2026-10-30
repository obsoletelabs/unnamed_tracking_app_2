"""Gaps closed by the ordinary-route authorization audit."""

import logging
import re
from io import StringIO

import httpx
import pytest
from sqlalchemy import delete, func, select

from src.database.models.plugin_permissions import PluginPermissionRequest
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notification_logging import NotificationLinkFilter, redact_request_target
from src.main import app
from tests.route_audit_support import create_tenants, live_matrix
from tests.test_public_apps import public_flow, sign_in  # noqa: F401


@pytest.mark.parametrize(
    ("method", "target", "status", "visible"),
    [
        ("POST", "/api/plugins/runtime/gateway", 200, False),
        ("POST", "/api/plugins/runtime/gateway?request=ordinary", 200, False),
        ("POST", "/api/plugins/runtime/gateway", 201, True),
        ("POST", "/api/plugins/runtime/gateway", 400, True),
        ("POST", "/api/plugins/runtime/gateway", 401, True),
        ("POST", "/api/plugins/runtime/gateway", 403, True),
        ("POST", "/api/plugins/runtime/gateway", 404, True),
        ("POST", "/api/plugins/runtime/gateway", 429, True),
        ("POST", "/api/plugins/runtime/gateway", 500, True),
        ("GET", "/api/plugins/runtime/gateway", 200, True),
        ("POST", "/api/plugins/runtime/gateway/other", 200, True),
        ("POST", "/api/auth/oidc/login/authentik", 200, True),
        ("GET", "/pwa/status", 200, False),
        ("GET", "/pwa/status?check=ordinary", 200, False),
        ("GET", "/pwa/status", 304, False),
        ("GET", "/manifest.webmanifest", 200, False),
        ("GET", "/manifest.webmanifest", 304, False),
        ("GET", "/service-worker.js", 200, False),
        (
            "GET",
            "/pwa/icons/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/192.png",
            200,
            False,
        ),
        (
            "GET",
            "/pwa/icons/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/512.png",
            304,
            False,
        ),
        ("GET", "/pwa/status", 201, True),
        ("GET", "/pwa/status", 401, True),
        ("GET", "/pwa/status", 403, True),
        ("GET", "/pwa/status", 429, True),
        ("GET", "/pwa/status", 500, True),
        ("GET", "/pwa/status", 503, True),
        ("GET", "/manifest.webmanifest", 404, True),
        ("GET", "/service-worker.js", 503, True),
        (
            "GET",
            "/pwa/icons/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/192.png",
            404,
            True,
        ),
        ("POST", "/pwa/status", 200, True),
        ("HEAD", "/pwa/status", 200, True),
        ("GET", "/pwa/status-debug", 200, True),
        ("GET", "/pwa/offline.html", 200, True),
        ("GET", "/pwa/icons/invalid/192.png", 200, True),
        ("GET", "/api/pwa/status", 200, True),
    ],
)
@pytest.mark.parametrize("level", [logging.INFO, logging.WARNING, logging.ERROR])
def test_access_log_quiets_only_routine_gateway_and_pwa_successes(
    method, target, status, visible, level
):
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(NotificationLinkFilter())
    record = logging.LogRecord(
        "uvicorn.access",
        level,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", method, target, "1.1", status),
        None,
    )
    handler.handle(record)
    assert bool(stream.getvalue()) is (visible or level != logging.INFO)


@pytest.fixture
async def world():
    tenants = await create_tenants()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        tenants.client = client
        yield tenants
    async with SessionLocal() as db:
        await db.execute(
            delete(User).where(
                User.id.in_(
                    [tenants.admin.user.id, tenants.attacker.user.id, tenants.victim.user.id]
                )
            )
        )
        await db.commit()


async def test_trusted_proxy_topology_needs_local_caller_or_administrator(world):
    url = "/api/internal/real-ip"
    anonymous = await world.client.get(url, headers={"X-Real-IP": "203.0.113.9"})
    assert anonymous.status_code == 401
    proxied = {"X-Real-IP": "203.0.113.9"}
    ordinary = await world.client.get(url, headers={**world.attacker.headers, **proxied})
    assert ordinary.status_code == 403
    admin = await world.client.get(url, headers={**world.admin.headers, **proxied})
    assert admin.status_code == 200 and set(admin.json()) == {"header", "trusted_proxies"}
    # The container's own renderer calls over loopback without proxy headers.
    local = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 5000)), base_url="http://test"
    )
    async with local:
        assert (await local.get(url)).status_code == 200
        forwarded = await local.get(url, headers={"X-Forwarded-For": "203.0.113.9"})
        assert forwarded.status_code == 401
    remote = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 5000)), base_url="http://test"
    )
    async with remote:
        assert (await remote.get(url)).status_code == 401


async def test_ordinary_users_cannot_queue_installation_wide_permission_proposals(world):
    payload = {
        "plugin_id": "audit.plugin",
        "installation_id": "8d1b3a0e-2c52-4b6c-9a6a-6c2a1bd4e0f1",
        "capability": "sessions.read",
        "capability_version": 1,
        "rationale": "Please approve",
    }
    async with SessionLocal() as db:
        before = await db.scalar(select(func.count()).select_from(PluginPermissionRequest))
    wide = await world.client.post(
        "/api/plugin-permissions/requests", headers=world.attacker.headers, json=payload
    )
    assert wide.status_code == 403
    other = await world.client.post(
        "/api/plugin-permissions/requests",
        headers=world.attacker.headers,
        json={**payload, "user_id": str(world.victim.user.id)},
    )
    assert other.status_code == 403
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(PluginPermissionRequest)) == before


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/api/calendar/feed/abcDEF123_-.ics", "/api/calendar/feed/[redacted].ics"),
        ("/api/auth/oidc/callback?code=c0de&state=s", "/api/auth/oidc/callback"),
        ("/api/auth/oidc/callback/corp?code=c0de&state=s", "/api/auth/oidc/callback/corp"),
        ("/api/notifications/email-verify?token=t", "/api/notifications/email-verify"),
        ("/api/game/list?search=title", "/api/game/list?search=title"),
        ("/api/calendar/feed-token", "/api/calendar/feed-token"),
    ],
)
def test_access_log_hides_bearer_values(target, expected):
    assert redact_request_target(target) == expected
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        "%s %s %s %s %s",
        ("c", "GET", target, "1.1", 200),
        None,
    )
    assert NotificationLinkFilter().filter(record)
    assert expected in record.getMessage()


async def test_first_run_setup_cannot_create_accounts_once_an_instance_has_users(world):
    response = await world.client.post(
        "/api/setup",
        json={
            "username": "intruder",
            "email": "intruder@example.test",
            "password": "Str0ng!pass-phrase",
        },
    )
    assert response.status_code == 409
    async with SessionLocal() as db:
        assert await db.scalar(select(User.id).where(User.username == "intruder")) is None


async def test_guest_sign_in_creates_no_account_and_no_account_claim_route_exists(
    public_flow,  # noqa: F811  (pytest fixture parameter)
):
    async with SessionLocal() as db:
        before = await db.scalar(select(func.count()).select_from(User))
    await sign_in(public_flow)
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == before
    matrix = live_matrix()
    served = {path for (_method, path) in matrix}
    # Claiming a queued Playnite request is not an account-claim operation,
    # and it requires the receiving desktop grant rather than a guest session.
    launch_claim = "/api/playnite/launch/v1/requests/claim"
    assert {p for p in served if re.search(r"claim|signup|sign-up", p)} == {launch_claim}
    assert matrix[("POST", launch_claim)] == "delegated-grant"
    # The only registration-shaped route registers a requester origin, never a person.
    assert [p for p in served if re.search(r"register", p)] == [
        "/api/delegation/v1/clients/register"
    ]
