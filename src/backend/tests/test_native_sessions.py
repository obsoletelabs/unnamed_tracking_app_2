"""Exercise built-in session management with real authentication and persisted SQL."""

import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.api.routes.session_manager import router
from src.core.auth import hash_token, session_cookie_name
from src.database.models.auth import UserSession
from src.database.models.user import User
from src.database.session import get_db


class SessionDb:
    """Adapt a persisted SQLite session to the route's asynchronous database interface."""

    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def commit(self):
        self.session.commit()


@pytest.fixture
def native_sessions():
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    UserSession.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as db:
        users = [
            User(
                id=uuid4(),
                username=name,
                email=f"{name}@example.test",
                password_hash="unused",
                is_admin=name == "admin",
            )
            for name in ("member", "admin")
        ]
        tokens = ["member-cookie", "admin-cookie"]
        now = int(time.time())
        rows = [
            UserSession(
                id=uuid4(),
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=now + 3600,
                created_at=now - 100,
                last_seen_at=now,
                ip_address="203.0.113.5",
                user_agent="Test browser",
            )
            for user, token in zip(users, tokens, strict=True)
        ]
        expired = UserSession(
            id=uuid4(),
            user_id=users[0].id,
            token_hash="expired-hash",
            expires_at=now - 1,
            created_at=now - 200,
            last_seen_at=now - 100,
        )
        db.add_all([*users, *rows, expired])
        db.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: SessionDb(db)
        yield SimpleNamespace(
            app=app, db=db, users=users, rows=rows, expired=expired, tokens=tokens
        )
    engine.dispose()


def client_for(boundary, user=0):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=boundary.app),
        base_url="http://test",
        cookies={session_cookie_name("test"): boundary.tokens[user]},
    )


@pytest.mark.asyncio
async def test_basic_lists_skip_geoip_and_keep_owner_and_admin_scopes(native_sessions, monkeypatch):
    def no_geoip():
        pytest.fail("basic session listing must not open GeoIP databases")

    monkeypatch.setattr("src.api.routes.session_manager.geoip.availability", no_geoip)
    async with client_for(native_sessions) as client:
        response = await client.get("/api/sessions/me?enriched=false")
        assert response.status_code == 200
        rows = response.json()
        assert {row["user_id"] for row in rows} == {str(native_sessions.users[0].id)}
        assert {row["state"] for row in rows} == {"active", "expired"}
        assert sum(row["is_current"] for row in rows) == 1
        assert rows[0]["ip_address"] == "203.0.113.5"
        assert rows[0]["created_at"] == native_sessions.rows[0].created_at
        assert not any(
            key in row for row in rows for key in ("location", "anomaly", "token", "token_hash")
        )
        assert (await client.get("/api/sessions/admin?enriched=false")).status_code == 403
    async with client_for(native_sessions, 1) as client:
        response = await client.get("/api/sessions/admin?enriched=false")
        assert response.status_code == 200
        assert len(response.json()) == 3
        current = [row for row in response.json() if row["is_current"]]
        assert [row["id"] for row in current] == [str(native_sessions.rows[1].id)]


@pytest.mark.asyncio
async def test_native_revocation_denies_foreign_sessions_and_ends_current_cookie(native_sessions):
    async with client_for(native_sessions) as client:
        foreign = native_sessions.rows[1]
        assert (await client.delete(f"/api/sessions/me/{foreign.id}")).status_code == 404
        native_sessions.db.refresh(foreign)
        assert foreign.revoked_at is None
        response = await client.delete("/api/sessions/me/all")
        assert response.status_code == 200 and response.json()["revoked"] == 2
        assert (await client.get("/api/sessions/me?enriched=false")).status_code == 401
        native_sessions.db.refresh(foreign)
        assert foreign.revoked_at is None


@pytest.mark.asyncio
async def test_admin_user_revocation_is_scoped_and_preserves_audit_rows(native_sessions):
    async with client_for(native_sessions, 1) as client:
        user_id = native_sessions.users[0].id
        assert (await client.delete(f"/api/sessions/admin/user/{user_id}")).json()["revoked"] == 2
        rows = (await client.get("/api/sessions/admin?enriched=false&state=revoked")).json()
        assert len(rows) == 2
        assert {row["user_id"] for row in rows} == {str(user_id)}
        assert all(row["revoked_at"] is not None for row in rows)


@pytest.mark.asyncio
async def test_legacy_enriched_response_is_preserved(native_sessions, monkeypatch):
    monkeypatch.setattr(
        "src.api.routes.session_manager.geoip.availability", lambda: {"city": False}
    )
    async with client_for(native_sessions) as client:
        rows = (await client.get("/api/sessions/me")).json()
        assert "location" in rows[0] and "anomaly" in rows[0]
