"""Authenticated bursts keep activity fresh without writing for every asset read."""

import time
from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import event, select, update

from src.core.auth import get_current_user, session_cookie_name
from src.database.models.auth import UserSession
from src.database.models.user import User
from src.database.session import SessionLocal, engine
from tests import test_auth_throttle_http as auth_http

account = auth_http.account
app = FastAPI()


@app.get("/read")
async def read_owned_resource(user: Annotated[User, Depends(get_current_user)]) -> dict[str, str]:
    return {"id": str(user.id)}


@pytest.fixture
def activity_writes():
    writes, commits = [], []

    def query(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("update user_sessions"):
            writes.append(True)

    def commit(_connection):
        commits.append(True)

    event.listen(engine.sync_engine, "before_cursor_execute", query)
    event.listen(engine.sync_engine, "commit", commit)
    try:
        yield writes, commits
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", query)
        event.remove(engine.sync_engine, "commit", commit)


def browser(account):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={session_cookie_name("test"): account.token},
    )


@pytest.mark.asyncio
async def test_fresh_browser_activity_needs_no_write_or_commit(account, activity_writes):
    writes, commits = activity_writes
    async with browser(account) as client:
        for _ in range(3):
            response = await client.get("/read")
            assert response.status_code == 200, response.text
            assert response.json()["id"] == str(account.id)
    assert writes == []
    assert commits == []


@pytest.mark.asyncio
async def test_stale_activity_is_committed_once_then_subsequent_reads_are_read_only(account):
    before = int(time.time())
    async with SessionLocal() as db:
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == account.id)
            .values(last_seen_at=before - 70)
        )
        await db.commit()
    async with browser(account) as client:
        assert (await client.get("/read")).status_code == 200
        async with SessionLocal() as db:
            touched = await db.scalar(
                select(UserSession.last_seen_at).where(UserSession.user_id == account.id)
            )
        assert touched >= before
        assert (await client.get("/read")).status_code == 200
        async with SessionLocal() as db:
            assert (
                await db.scalar(
                    select(UserSession.last_seen_at).where(UserSession.user_id == account.id)
                )
                == touched
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawal", ["revoked", "expired", "inactive"])
async def test_each_fresh_read_still_checks_live_credential_state(account, withdrawal):
    async with browser(account) as client:
        assert (await client.get("/read")).status_code == 200
        async with SessionLocal() as db:
            if withdrawal == "inactive":
                await db.execute(update(User).where(User.id == account.id).values(is_active=False))
            else:
                values = (
                    {"revoked_at": int(time.time())}
                    if withdrawal == "revoked"
                    else {"expires_at": 1}
                )
                await db.execute(
                    update(UserSession).where(UserSession.user_id == account.id).values(**values)
                )
            await db.commit()
        response = await client.get("/read")
        assert response.status_code == 401
