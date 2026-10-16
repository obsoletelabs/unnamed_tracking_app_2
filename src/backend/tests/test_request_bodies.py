"""Large request bodies only reach routes for callers with verified credentials."""

import secrets
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete

from src.api import request_bodies
from src.core.auth import hash_token, session_cookie_name
from src.database.models.auth import UserSession
from src.database.models.user import User
from src.database.session import SessionLocal

LARGE = b"x" * (request_bodies.ANONYMOUS_BODY_LIMIT + 1)


@pytest.fixture
def received():
    bodies: list[bytes] = []

    async def application(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        bodies.append(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"read"})

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=request_bodies.AnonymousBodyLimitMiddleware(application)),
        base_url="http://test",
    )
    return client, bodies


@pytest.fixture
async def session_token():
    token, now = secrets.token_urlsafe(32), int(time.time())
    async with SessionLocal() as db:
        user = User(
            username=f"body_{uuid4().hex[:10]}",
            email=f"{uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.flush()
        db.add(
            UserSession(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=now + 3600,
                created_at=now,
                last_seen_at=now,
            )
        )
        await db.commit()
        identity = user.id
    yield token
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == identity))
        await db.commit()


async def test_small_anonymous_bodies_pass_through(received):
    client, bodies = received
    async with client:
        response = await client.post("/api/auth/login", content=b"x" * 1024)
    assert response.status_code == 200 and bodies == [b"x" * 1024]


@pytest.mark.parametrize(
    "headers",
    [{}, {"authorization": "Bearer utk_not-a-real-key"}, {"cookie": "unknown=session"}],
)
async def test_large_anonymous_bodies_never_reach_the_route(received, headers):
    client, bodies = received
    async with client:
        response = await client.post("/api/game/1/screenshots", content=LARGE, headers=headers)
    assert response.status_code == 413
    assert response.json()["detail"] == "Sign in to send a request body larger than 1 MB."
    assert bodies == []


async def test_unsized_anonymous_bodies_never_reach_the_route(received):
    client, bodies = received

    async def chunks():
        yield b"x"

    async with client:
        response = await client.post("/api/auth/login", content=chunks())
    assert response.status_code == 413 and bodies == []


async def test_large_bodies_with_a_valid_session_are_read(received, session_token):
    client, bodies = received
    client.cookies.set(session_cookie_name("test"), session_token)
    async with client:
        response = await client.post("/api/game/1/screenshots", content=LARGE)
    assert response.status_code == 200 and bodies == [LARGE]


async def test_large_bodies_from_the_plugin_runtime_are_read(received, monkeypatch):
    client, bodies = received
    monkeypatch.setenv("PLUGIN_RUNTIME_TOKEN", "runtime-token")
    async with client:
        refused = await client.post(
            "/api/plugins/runtime/gateway",
            content=LARGE,
            headers={"x-plugin-runtime-token": "wrong"},
        )
        accepted = await client.post(
            "/api/plugins/runtime/gateway",
            content=LARGE,
            headers={"x-plugin-runtime-token": "runtime-token"},
        )
    assert (refused.status_code, accepted.status_code) == (413, 200)
    assert bodies == [LARGE]


async def test_only_api_requests_are_checked(received):
    client, bodies = received
    async with client:
        response = await client.post("/not-api", content=LARGE)
    assert response.status_code == 200 and bodies == [LARGE]


async def test_application_refuses_large_anonymous_login_bodies():
    from src.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/auth/login", content=LARGE, headers={"content-type": "application/json"}
        )
    assert response.status_code == 413
