"""Session introspection and profile writes never return stored provider credentials."""

import httpx
import pytest
from sqlalchemy import select, update

from src.core.auth import session_cookie_name
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app
from tests import test_auth_throttle_http as auth_http

account = auth_http.account


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_profile_reports_configuration_without_disclosing_secret(account, configured):
    secret = "test-private-provider-secret"
    async with SessionLocal() as db:
        await db.execute(
            update(User)
            .where(User.id == account.id)
            .values(steamgriddb_api_key=secret if configured else None)
        )
        await db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={session_cookie_name("test"): account.token},
    ) as client:
        response = await client.get("/api/auth/me")
        assert response.status_code == 200, response.text
        assert response.json()["steamgriddb_api_key"] is None
        assert response.json()["steamgriddb_configured"] is configured
        assert secret not in response.text
        replacement = "test-replacement-provider-secret"
        response = await client.patch("/api/auth/me", json={"steamgriddb_api_key": replacement})
        assert response.status_code == 200, response.text
        assert response.json()["steamgriddb_api_key"] is None
        assert response.json()["steamgriddb_configured"] is True
        assert replacement not in response.text
    async with SessionLocal() as db:
        assert (
            await db.scalar(select(User.steamgriddb_api_key).where(User.id == account.id))
            == replacement
        )
