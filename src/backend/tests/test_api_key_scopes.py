"""API key scopes: legacy keys keep ordinary access but never administrator access (#415)."""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete

from src.core.auth import acts_as_admin, create_api_key, validate_api_key_scopes
from src.database.models.auth import UserApiKey
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app


async def _admin_with_key(scopes):
    raw, prefix, digest = create_api_key()
    async with SessionLocal() as db:
        user = User(
            username=f"scoped_{uuid4().hex[:10]}",
            email=f"{uuid4()}@example.test",
            password_hash="x",
            is_active=True,
            is_admin=True,
        )
        db.add(user)
        await db.flush()
        db.add(
            UserApiKey(user_id=user.id, name="k", key_prefix=prefix, key_hash=digest, scopes=scopes)
        )
        await db.commit()
        return user.id, {"Authorization": "Bearer " + raw}


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


@pytest.fixture
async def made():
    ids = []
    yield ids
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_(ids)))
        await db.commit()


ADMIN_ROUTE = "/api/auth/users"
USER_ROUTE = "/api/auth/me"


async def test_a_legacy_key_of_an_administrator_has_user_access_but_not_admin_access(client, made):
    user_id, headers = await _admin_with_key([])
    made.append(user_id)
    assert (await client.get(USER_ROUTE, headers=headers)).status_code == 200
    assert (await client.get(ADMIN_ROUTE, headers=headers)).status_code == 403


async def test_the_admin_scope_lets_an_administrators_key_reach_admin_routes(client, made):
    user_id, headers = await _admin_with_key(["read", "admin"])
    made.append(user_id)
    assert (await client.get(ADMIN_ROUTE, headers=headers)).status_code == 200


async def test_a_read_key_cannot_make_changes(client, made):
    user_id, headers = await _admin_with_key(["read"])
    made.append(user_id)
    assert (await client.get(USER_ROUTE, headers=headers)).status_code == 200
    refused = await client.patch(USER_ROUTE, json={"display_name": "x"}, headers=headers)
    assert refused.status_code == 403
    assert "not allowed" in refused.json()["detail"]


async def test_a_write_key_can_make_changes_but_still_not_admin_ones(client, made):
    user_id, headers = await _admin_with_key(["write"])
    made.append(user_id)
    changed = await client.patch(USER_ROUTE, json={"display_name": "Scoped"}, headers=headers)
    assert changed.status_code == 200
    assert (await client.get(ADMIN_ROUTE, headers=headers)).status_code == 403


def test_scope_validation():
    assert validate_api_key_scopes([]) == []
    assert validate_api_key_scopes(["write", "read", "read"]) == ["read", "write"]
    assert validate_api_key_scopes(["api.full"]) == ["api.full"]
    for bad in (["superuser"], ["admin"], ["readonly"]):
        with pytest.raises(ValueError):
            validate_api_key_scopes(bad)


def test_acts_as_admin_needs_an_admin_row_that_is_not_withheld():
    user = User(username="a", email="a@x.test", password_hash="x", is_admin=True)
    assert acts_as_admin(user) is True
    user._admin_withheld = True  # pylint: disable=protected-access
    assert acts_as_admin(user) is False
    assert acts_as_admin(User(username="b", email="b@x.test", password_hash="x")) is False


async def test_keys_with_old_free_form_labels_keep_their_ordinary_access(client, made):
    user_id, headers = await _admin_with_key(["games.read"])
    made.append(user_id)
    assert (await client.get(USER_ROUTE, headers=headers)).status_code == 200
    changed = await client.patch(USER_ROUTE, json={"display_name": "Old"}, headers=headers)
    assert changed.status_code == 200
    assert (await client.get(ADMIN_ROUTE, headers=headers)).status_code == 403
