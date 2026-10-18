"""Desktop apps (Playnite) sign in through user-approved integration access, not API keys."""

import secrets
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from src.api.routes.delegation import browser_user
from src.core.auth import hash_token
from src.database.models.delegation import IntegrationClient, IntegrationPolicy
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.delegation import integration_policy
from src.main import app

BASE = "/api/delegation/v1"
SYNC = ["library.sync", "saves.read", "saves.write"]


@pytest.fixture
async def desktop():
    async with SessionLocal() as db:
        policy = await integration_policy(db)
        before = (policy.enabled, policy.allow_desktop_apps)
        # Web integrations stay off: desktop sign-in must not depend on them.
        policy.enabled, policy.allow_desktop_apps = False, True
        users = [
            User(
                username=f"desktop_{uuid4().hex[:10]}",
                email=f"{uuid4()}@example.test",
                password_hash="x",
                is_active=True,
            )
            for _ in range(2)
        ]
        db.add_all(users)
        await db.flush()
        games = [
            Game(
                user_id=user.id,
                title="Hades",
                sort_title="hades",
                folder_location=f"desktop_{uuid4().hex}",
                status=GameStatus.BACKLOG,
            )
            for user in users
        ]
        db.add_all(games)
        await db.commit()
        flow = SimpleNamespace(
            user=users[0], other=users[1], game_id=games[0].id, other_game_id=games[1].id
        )
    app.dependency_overrides[browser_user] = lambda: flow.user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        flow.client = client
        yield flow
    app.dependency_overrides.pop(browser_user, None)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_([flow.user.id, flow.other.id])))
        policy = await db.get(IntegrationPolicy, 1)
        policy.enabled, policy.allow_desktop_apps = before
        await db.commit()


async def connect(flow, scopes=SYNC, *, approve=None):
    verifier = secrets.token_urlsafe(48)
    started = await flow.client.post(
        f"{BASE}/desktop/playnite/requests",
        json={
            "scopes": scopes,
            "code_challenge": hash_token(verifier),
            "device_name": "Gaming PC",
            "device_id": "playnite-1234abcd",
        },
    )
    assert started.status_code == 201, started.text
    request_id = started.json()["request_id"]
    assert started.json()["verification_path"] == f"/integration-access/{request_id}"
    pending = await flow.client.post(
        f"{BASE}/requests/{request_id}/exchange", json={"verifier": verifier}
    )
    assert pending.json() == {"state": "pending"}
    summary = (await flow.client.get(f"{BASE}/requests/{request_id}")).json()
    assert summary["kind"] == "desktop"
    assert {c["scope"]: c["write"] for c in summary["capabilities"]}["library.sync"] is True
    assert summary["external_account"]["username"] == "Gaming PC"
    decided = await flow.client.post(
        f"{BASE}/requests/{request_id}/decision",
        json={"approve": True, "scopes": approve or scopes, "expires_in_hours": None},
    )
    assert decided.status_code == 200, decided.text
    exchanged = await flow.client.post(
        f"{BASE}/requests/{request_id}/exchange", json={"verifier": verifier}
    )
    body = exchanged.json()
    assert body["state"] == "connected" and body["expires_at"] is None
    return {"Authorization": f"Bearer {body['access_token']}"}


async def test_playnite_connects_without_an_api_key_and_syncs_its_library(desktop):
    headers = await connect(desktop)
    contract = await desktop.client.get("/api/playnite/contract", headers=headers)
    assert contract.status_code == 200
    created = await desktop.client.post(
        "/api/game/create",
        headers=headers,
        json={
            "title": "Celeste",
            "folder_location": f"celeste_{uuid4().hex[:8]}",
            "playnite_guid": str(uuid4()),
        },
    )
    assert created.status_code == 201, created.text
    listed = (await desktop.client.get("/api/game/list", headers=headers)).json()
    assert {game["title"] for game in listed} == {"Hades", "Celeste"}  # only its own user
    updated = await desktop.client.patch(
        f"/api/game/update/{created.json()['id']}", headers=headers, json={"favorite": True}
    )
    assert updated.status_code == 200 and updated.json()["favorite"] is True
    saves = await desktop.client.get(f"/api/game/{desktop.game_id}/archives/save", headers=headers)
    assert saves.status_code == 200 and saves.json() == []


async def test_an_app_credential_reaches_only_the_routes_its_scopes_name(desktop):
    headers = await connect(
        desktop, ["library.sync", "saves.read", "saves.write"], approve=["library.sync"]
    )
    assert (await desktop.client.get("/api/game/list", headers=headers)).status_code == 200
    # saves were requested but not approved
    saves = await desktop.client.get(f"/api/game/{desktop.game_id}/archives/save", headers=headers)
    assert saves.status_code == 403
    # never an ordinary user credential elsewhere
    for path in ("/api/auth/me", f"/api/game/{desktop.game_id}/files/doc", "/api/settings/jobs"):
        assert (await desktop.client.get(path, headers=headers)).status_code == 403, path
    # and never another user's records
    other = await desktop.client.patch(
        f"/api/game/update/{desktop.other_game_id}", headers=headers, json={"favorite": True}
    )
    assert other.status_code == 404


async def test_revoking_or_switching_off_desktop_apps_blocks_the_credential(desktop):
    headers = await connect(desktop)
    assert (await desktop.client.get("/api/game/list", headers=headers)).status_code == 200
    async with SessionLocal() as db:
        policy = await db.get(IntegrationPolicy, 1)
        policy.allow_desktop_apps = False
        await db.commit()
    assert (await desktop.client.get("/api/game/list", headers=headers)).status_code == 401
    refused = await desktop.client.post(
        f"{BASE}/desktop/playnite/requests",
        json={
            "scopes": SYNC,
            "code_challenge": "0" * 64,
            "device_name": "PC",
            "device_id": "playnite-1234abcd",
        },
    )
    assert refused.status_code == 403
    async with SessionLocal() as db:
        policy = await db.get(IntegrationPolicy, 1)
        policy.allow_desktop_apps = True
        await db.commit()
    grants = (await desktop.client.get(f"{BASE}/grants")).json()
    grant = next(g for g in grants if g["kind"] == "desktop")
    assert grant["external_account"]["username"] == "Gaming PC"
    assert (await desktop.client.delete(f"{BASE}/grants/{grant['id']}")).status_code == 200
    assert (await desktop.client.get("/api/game/list", headers=headers)).status_code == 401


async def test_unknown_desktop_apps_and_disabled_desktop_clients_are_refused(desktop):
    body = {
        "scopes": SYNC,
        "code_challenge": "0" * 64,
        "device_name": "PC",
        "device_id": "device-1234abcd",
    }
    assert (
        await desktop.client.post(f"{BASE}/desktop/other/requests", json=body)
    ).status_code == 404
    await desktop.client.post(f"{BASE}/desktop/playnite/requests", json=body)
    async with SessionLocal() as db:
        row = await db.scalar(
            select(IntegrationClient).where(IntegrationClient.origin == "desktop:playnite")
        )
        row.enabled = False
        await db.commit()
    try:
        response = await desktop.client.post(f"{BASE}/desktop/playnite/requests", json=body)
        assert response.status_code == 403
    finally:
        async with SessionLocal() as db:
            row = await db.scalar(
                select(IntegrationClient).where(IntegrationClient.origin == "desktop:playnite")
            )
            row.enabled = True
            await db.commit()


async def test_instance_discovery_lists_desktop_apps(desktop):
    instance = (await desktop.client.get(f"{BASE}/instance")).json()
    assert instance["desktop_apps"] == ["playnite"]
    assert instance["clients"] == []  # web integrations are still off


async def test_an_operator_can_disable_and_re_allow_the_desktop_client(desktop):
    from src.core.auth import get_current_admin

    headers = await connect(desktop)
    app.dependency_overrides[get_current_admin] = lambda: desktop.user
    try:
        off = await desktop.client.put(
            f"{BASE}/clients",
            json={"name": "Unnamed Tracking for Playnite", "origin": "desktop:playnite"},
        )
        assert off.status_code == 200, off.text
        assert (await desktop.client.get("/api/game/list", headers=headers)).status_code == 401
        listed = (await desktop.client.get(f"{BASE}/policy")).json()["clients"]
        assert any(c["kind"] == "desktop" and not c["enabled"] for c in listed)
        on = await desktop.client.put(
            f"{BASE}/clients",
            json={
                "name": "Unnamed Tracking for Playnite",
                "origin": "desktop:playnite",
                "enabled": True,
            },
        )
        assert on.status_code == 200
        assert (await desktop.client.get("/api/game/list", headers=headers)).status_code == 200
        bad = await desktop.client.put(
            f"{BASE}/clients", json={"name": "x", "origin": "desktop:other"}
        )
        assert bad.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_admin, None)
