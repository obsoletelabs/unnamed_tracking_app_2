"""Real HTTP and PostgreSQL coverage for the user integration boundary."""

import asyncio
import hashlib
import secrets
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete

from src.api.routes.delegation import browser_user
from src.core.auth import create_api_key, hash_token
from src.database.models.achievement import Achievement
from src.database.models.auth import UserApiKey
from src.database.models.delegation import DelegatedGrant, IntegrationClient, IntegrationPolicy
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.delegation import integration_policy
from src.main import app

BASE = "/api/delegation/v1"


@pytest.fixture
async def delegation_flow():
    async with SessionLocal() as db:
        policy = await integration_policy(db)
        previous_enabled = policy.enabled
        policy.enabled = True
        users = [
            User(
                username=f"delegation_{uuid4().hex[:10]}",
                email=f"{uuid4()}@example.test",
                password_hash="x",
                is_active=True,
                is_admin=index == 0,
            )
            for index in range(2)
        ]
        db.add_all(users)
        client = IntegrationClient(
            name="Game night", origin=f"https://{uuid4().hex}.example.test", enabled=True
        )
        db.add(client)
        await db.flush()
        games = [
            Game(
                user_id=user.id,
                title=f"Private game {index}",
                sort_title=f"private {index}",
                folder_location=f"delegation_{uuid4().hex}",
                status=GameStatus.BACKLOG,
            )
            for index, user in enumerate(users)
        ]
        db.add_all(games)
        await db.commit()
        flow = SimpleNamespace(
            user=users[0],
            other=users[1],
            game_id=games[0].id,
            other_game_id=games[1].id,
            integration_id=client.id,
        )
    app.dependency_overrides[browser_user] = lambda: flow.user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        flow.client = client
        yield flow
    app.dependency_overrides.pop(browser_user, None)
    async with SessionLocal() as db:
        await db.execute(
            delete(IntegrationClient).where(IntegrationClient.id == flow.integration_id)
        )
        await db.execute(delete(User).where(User.id.in_([flow.user.id, flow.other.id])))
        policy = await db.get(IntegrationPolicy, 1)
        policy.enabled = previous_enabled
        await db.commit()


async def request_access(flow, scopes):
    verifier = secrets.token_urlsafe(48)
    response = await flow.client.post(
        BASE + "/requests",
        json={
            "client_id": str(flow.integration_id),
            "scopes": scopes,
            "code_challenge": hash_token(verifier),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["request_id"], verifier


async def grant_access(flow, scopes, *, hours=1):
    request_id, verifier = await request_access(flow, scopes)
    decision = await flow.client.post(
        f"{BASE}/requests/{request_id}/decision",
        json={
            "approve": True,
            "scopes": scopes,
            "expires_in_hours": hours,
        },
    )
    assert decision.status_code == 200, decision.text
    response = await flow.client.post(
        f"{BASE}/requests/{request_id}/exchange", json={"verifier": verifier}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    return {"Authorization": "Bearer " + result["access_token"]}, UUID(result["grant_id"])


async def test_external_account_label_is_visible_before_and_after_consent(delegation_flow):
    flow = delegation_flow
    label = {"provider": "game-night", "subject": "discord-id", "username": "iceman2018"}
    verifier = secrets.token_urlsafe(48)
    result = await flow.client.post(
        BASE + "/requests",
        json={
            "client_id": str(flow.integration_id),
            "scopes": ["games.read"],
            "code_challenge": hash_token(verifier),
            "external_account": label,
        },
    )
    request_id = result.json()["request_id"]
    assert (await flow.client.get(f"{BASE}/requests/{request_id}")).json()[
        "external_account"
    ] == label
    assert (
        await flow.client.post(
            f"{BASE}/requests/{request_id}/decision",
            json={
                "approve": True,
                "scopes": ["games.read"],
            },
        )
    ).status_code == 200
    grants = (await flow.client.get(BASE + "/grants")).json()
    assert grants[0]["external_account"] == label
    assert grants[0]["scopes"] == ["games.read"]  # A label confers no authority.


async def test_guest_retention_can_exceed_thirty_days_without_changing_grants():
    from src.api.schemas.delegation import PolicyWrite

    assert PolicyWrite(enabled=True, guest_retention_hours=8760).maximum_grant_hours == 336
    with pytest.raises(ValueError):
        PolicyWrite(enabled=True, guest_retention_hours=8761)


async def test_open_registration_still_requires_owner_policy_and_user_consent(delegation_flow):
    flow = delegation_flow
    payload = {"name": "External client", "origin": f"https://{uuid4().hex}.example.test"}
    async with SessionLocal() as db:
        policy = await integration_policy(db)
        previous = policy.allow_any_origin
        policy.allow_any_origin = False
        await db.commit()
    try:
        assert (await flow.client.post(BASE + "/clients/register", json=payload)).status_code == 403
        async with SessionLocal() as db:
            policy = await integration_policy(db)
            policy.allow_any_origin = True
            await db.commit()
        response = await flow.client.post(BASE + "/clients/register", json=payload)
        assert response.status_code == 201
        registered = response.json()
        assert set(registered) == {"id", "name", "origin"}
        assert (await flow.client.get(BASE + "/games")).status_code == 401
        # Registration cannot rename or re-enable an individually disabled origin.
        async with SessionLocal() as db:
            client = await db.get(IntegrationClient, UUID(registered["id"]))
            client.enabled = False
            await db.commit()
        response = await flow.client.post(
            BASE + "/clients/register", json={**payload, "name": "Replacement"}
        )
        assert response.status_code == 403
        for origin in (
            "http://example.com",
            "https://user:pass@example.com",
            "https://example.com/path",
        ):
            assert (
                await flow.client.post(
                    BASE + "/clients/register", json={**payload, "origin": origin}
                )
            ).status_code == 422
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(IntegrationClient).where(IntegrationClient.origin == payload["origin"])
            )
            policy = await integration_policy(db)
            policy.allow_any_origin = previous
            await db.commit()


async def test_exchange_is_verifier_bound_and_one_use(delegation_flow):
    flow = delegation_flow
    request_id, verifier = await request_access(flow, ["games.read"])
    endpoint = f"{BASE}/requests/{request_id}/exchange"
    assert (
        await flow.client.post(endpoint, json={"verifier": secrets.token_urlsafe(48)})
    ).status_code == 401
    assert (await flow.client.post(endpoint, json={"verifier": verifier})).json() == {
        "state": "pending"
    }
    assert (
        await flow.client.post(
            f"{BASE}/requests/{request_id}/decision",
            json={
                "approve": True,
                "scopes": ["games.read"],
                "expires_in_hours": 1,
            },
        )
    ).status_code == 200
    exchanged = await flow.client.post(endpoint, json={"verifier": verifier})
    assert exchanged.status_code == 200
    assert exchanged.headers["cache-control"] == "no-store"
    assert (await flow.client.post(endpoint, json={"verifier": verifier})).status_code == 409


async def test_consent_cannot_elevate_request_or_be_reassigned(delegation_flow):
    flow = delegation_flow
    request_id, _ = await request_access(flow, ["games.read"])
    endpoint = f"{BASE}/requests/{request_id}/decision"
    assert (
        await flow.client.post(endpoint, json={"approve": True, "scopes": ["tracking.write"]})
    ).status_code == 422
    assert (
        await flow.client.post(endpoint, json={"approve": True, "scopes": ["games.read"]})
    ).status_code == 200
    app.dependency_overrides[browser_user] = lambda: flow.other
    assert (
        await flow.client.post(endpoint, json={"approve": True, "scopes": ["games.read"]})
    ).status_code == 409


async def test_read_grant_is_owned_and_cannot_write_even_for_admin(delegation_flow):
    flow = delegation_flow
    headers, _ = await grant_access(flow, ["games.read"])
    assert (
        await flow.client.get(f"{BASE}/games/{flow.game_id}", headers=headers)
    ).status_code == 200
    assert (
        await flow.client.get(f"{BASE}/games/{flow.other_game_id}", headers=headers)
    ).status_code == 404
    library = (await flow.client.get(BASE + "/games", headers=headers)).json()["games"]
    assert [row["id"] for row in library] == [str(flow.game_id)]
    assert "folder_location" not in library[0]
    assert library[0]["offer_status"] == "unavailable"
    assert (
        await flow.client.patch(
            f"{BASE}/games/{flow.game_id}/tracking", headers=headers, json={"favorite": True}
        )
    ).status_code == 403
    for endpoint in ("/api/auth/users", "/api/auth/me", "/api/plugins", BASE + "/policy"):
        assert (await flow.client.get(endpoint, headers=headers)).status_code == 403
    assert (
        await flow.client.post(
            "/api/auth/api-keys", headers=headers, json={"name": "attempt", "scopes": []}
        )
    ).status_code == 403


async def test_tracking_write_has_field_and_object_limits(delegation_flow):
    flow = delegation_flow
    headers, _ = await grant_access(flow, ["tracking.write"])
    own = f"{BASE}/games/{flow.game_id}/tracking"
    assert (
        await flow.client.patch(own, headers=headers, json={"favorite": True})
    ).status_code == 200
    assert (
        await flow.client.patch(own, headers=headers, json={"folder_location": "stolen"})
    ).status_code == 422
    assert (
        await flow.client.patch(own, headers=headers, json={"user_id": str(flow.other.id)})
    ).status_code == 422
    assert (
        await flow.client.patch(
            f"{BASE}/games/{flow.other_game_id}/tracking", headers=headers, json={"favorite": True}
        )
    ).status_code == 404
    async with SessionLocal() as db:
        assert (await db.get(Game, flow.game_id)).favorite is True
        assert (await db.get(Game, flow.other_game_id)).favorite is False


async def test_multiplayer_settings_need_separate_consent_and_own_game(delegation_flow):
    flow = delegation_flow
    endpoint = f"{BASE}/games/{flow.game_id}/play-compatibility"
    payload = {"play_compatibility": {}}
    headers, _ = await grant_access(flow, ["tracking.write"])
    assert (await flow.client.patch(endpoint, headers=headers, json=payload)).status_code == 403
    headers, grant_id = await grant_access(flow, ["compatibility.write", "games.read"])
    result = await flow.client.patch(endpoint, headers=headers, json=payload)
    assert result.status_code == 200
    assert result.json()["play_compatibility"]["min_players"] is None
    assert (
        await flow.client.patch(
            f"{BASE}/games/{flow.other_game_id}/play-compatibility",
            headers=headers,
            json=payload,
        )
    ).status_code == 404
    assert (
        await flow.client.patch(endpoint, headers=headers, json={**payload, "favorite": True})
    ).status_code == 422
    assert (
        await flow.client.patch(endpoint, headers=headers, json={"play_compatibility": None})
    ).status_code == 200
    app.dependency_overrides[browser_user] = lambda: flow.user
    assert (
        await flow.client.patch(f"{BASE}/grants/{grant_id}", json={"scopes": ["games.read"]})
    ).status_code == 200
    assert (await flow.client.patch(endpoint, headers=headers, json=payload)).status_code == 403


@pytest.mark.parametrize("hours", [1, None])
@pytest.mark.parametrize("disabled", ["revocation", "expiry", "client", "owner_policy", "account"])
async def test_live_authority_is_rechecked(delegation_flow, disabled, hours):
    flow = delegation_flow
    headers, grant_id = await grant_access(flow, ["games.read"], hours=hours)
    async with SessionLocal() as db:
        if disabled == "revocation":
            (await db.get(DelegatedGrant, grant_id)).revoked_at = int(time.time())
        elif disabled == "expiry":
            (await db.get(DelegatedGrant, grant_id)).expires_at = int(time.time()) - 1
        elif disabled == "client":
            (await db.get(IntegrationClient, flow.integration_id)).enabled = False
        elif disabled == "owner_policy":
            (await db.get(IntegrationPolicy, 1)).enabled = False
        else:
            (await db.get(User, flow.user.id)).is_active = False
        await db.commit()
    assert (await flow.client.get(BASE + "/games", headers=headers)).status_code == 401


async def test_ongoing_consent_survives_time_and_rotation_until_user_revokes(
    delegation_flow, monkeypatch
):
    flow = delegation_flow
    headers, grant_id = await grant_access(flow, ["games.read", "profile.read"], hours=None)
    async with SessionLocal() as db:
        assert (await db.get(DelegatedGrant, grant_id)).expires_at is None
    grants = (await flow.client.get(BASE + "/grants")).json()
    assert grants[0]["expires_at"] is None
    future = int(time.time()) + 365 * 86400
    monkeypatch.setattr("src.features.delegation.time.time", lambda: future)
    assert (await flow.client.get(BASE + "/games", headers=headers)).status_code == 200
    rotated = await flow.client.post(BASE + "/credential/rotate", headers=headers)
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["expires_at"] is None
    assert (await flow.client.get(BASE + "/games", headers=headers)).status_code == 401
    headers = {"Authorization": "Bearer " + rotated.json()["access_token"]}
    assert (await flow.client.get(BASE + "/games", headers=headers)).status_code == 200
    assert (await flow.client.delete(f"{BASE}/grants/{grant_id}")).status_code == 200
    assert (await flow.client.get(BASE + "/games", headers=headers)).status_code == 401


async def test_user_can_reduce_and_revoke_grants(delegation_flow):
    flow = delegation_flow
    headers, grant_id = await grant_access(flow, ["games.read", "profile.read"])
    endpoint = f"{BASE}/grants/{grant_id}"
    assert (
        await flow.client.patch(endpoint, json={"scopes": ["tracking.write"]})
    ).status_code == 422
    assert (await flow.client.patch(endpoint, json={"scopes": ["profile.read"]})).status_code == 200
    assert (await flow.client.get(BASE + "/games", headers=headers)).status_code == 403
    assert (await flow.client.get(BASE + "/profile", headers=headers)).status_code == 200
    app.dependency_overrides[browser_user] = lambda: flow.other
    assert (await flow.client.delete(endpoint)).status_code == 404
    app.dependency_overrides[browser_user] = lambda: flow.user
    assert (await flow.client.delete(endpoint)).status_code == 200
    assert (await flow.client.get(BASE + "/profile", headers=headers)).status_code == 401


async def test_achievement_report_survives_provider_reimport_and_stays_self_reported(
    delegation_flow,
):
    flow = delegation_flow
    headers, _ = await grant_access(flow, ["achievements.read", "achievements.write"])
    async with SessionLocal() as db:
        row = Achievement(
            game_id=flow.game_id,
            provider="steam",
            external_id="ACH_1",
            name="First step",
            unlocked=False,
        )
        db.add(row)
        await db.commit()
        original_id = row.id
    endpoint = f"{BASE}/games/{flow.game_id}"
    report = await flow.client.post(
        endpoint + "/achievement-reports",
        headers=headers,
        json={"provider": "steam", "external_id": "ACH_1", "unlocked": True},
    )
    assert report.status_code == 200, report.text
    assert report.json()["verified"] is False
    assert report.json()["provenance"] == "self_reported_override"
    async with SessionLocal() as db:
        assert (await db.get(Achievement, original_id)).unlocked is False
        await db.execute(delete(Achievement).where(Achievement.id == original_id))
        db.add(
            Achievement(
                game_id=flow.game_id,
                provider="steam",
                external_id="ACH_1",
                name="First step",
                unlocked=False,
            )
        )
        await db.commit()
    result = (await flow.client.get(endpoint + "/achievements", headers=headers)).json()[
        "achievements"
    ][0]
    assert result["id"] != str(original_id)
    assert result["unlocked"] is False
    assert result["local_report"]["unlocked"] is True
    assert (
        await flow.client.post(
            endpoint + "/achievement-reports",
            headers=headers,
            json={
                "provider": "steam",
                "external_id": "ACH_1",
                "name": "Fake verified name",
                "unlocked": True,
            },
        )
    ).status_code == 422
    assert (
        await flow.client.post(
            f"{BASE}/games/{flow.other_game_id}/achievement-reports",
            headers=headers,
            json={"name": "Foreign", "unlocked": True},
        )
    ).status_code == 404


async def test_manual_achievements_and_wishlist_ownership(delegation_flow):
    flow = delegation_flow
    headers, _ = await grant_access(flow, ["games.read", "achievements.read", "achievements.write"])
    async with SessionLocal() as db:
        (await db.get(Game, flow.game_id)).status = GameStatus.WISHLIST
        await db.commit()
    assert (await flow.client.get(f"{BASE}/games/{flow.game_id}", headers=headers)).json()[
        "owned"
    ] is False
    endpoint = f"{BASE}/games/{flow.game_id}/achievement-reports"
    result = await flow.client.post(
        endpoint, headers=headers, json={"name": "Complete with friends", "unlocked": True}
    )
    assert result.status_code == 200
    assert result.json()["provenance"] == "manual"
    assert result.json()["verified"] is False


async def test_legacy_api_key_behavior_is_preserved(delegation_flow):
    flow = delegation_flow
    raw, prefix, digest = create_api_key()
    async with SessionLocal() as db:
        db.add(
            UserApiKey(
                user_id=flow.user.id,
                name="Legacy",
                key_prefix=prefix,
                key_hash=digest,
                scopes=["games.read"],
            )
        )
        await db.commit()
    headers = {"Authorization": "Bearer " + raw}
    assert (
        await flow.client.patch(
            f"/api/game/update/{flow.game_id}", headers=headers, json={"favorite": True}
        )
    ).status_code == 200
    # ordinary access is preserved; administrator access now needs the `admin` scope
    assert (await flow.client.get("/api/auth/users", headers=headers)).status_code == 403
    assert (await flow.client.get(BASE + "/games", headers=headers)).status_code == 401


async def test_rotation_preserves_grants_and_invalidates_old_credential(delegation_flow):
    flow = delegation_flow
    headers, grant_id = await grant_access(flow, ["profile.read"])
    response = await flow.client.post(BASE + "/credential/rotate", headers=headers)
    assert response.status_code == 200
    rotated = response.json()
    assert rotated["scopes"] == ["profile.read"]
    assert (await flow.client.get(BASE + "/profile", headers=headers)).status_code == 401
    replacement = {"Authorization": "Bearer " + rotated["access_token"]}
    assert (await flow.client.get(BASE + "/profile", headers=replacement)).status_code == 200
    async with SessionLocal() as db:
        assert (await db.get(DelegatedGrant, grant_id)).expires_at == rotated["expires_at"]


async def test_library_marks_copies_of_one_game_for_integrations(delegation_flow):
    """#274: an integration can fold a user's copies and match them by the game's main title."""
    from src.features.game_identity import decisions

    flow = delegation_flow
    async with SessionLocal() as db:
        copy = Game(
            user_id=flow.user.id,
            title="Private game 0 for Windows",
            sort_title="private game 0 for windows",
            folder_location=f"delegation_{uuid4().hex}",
            status=GameStatus.PLAYING,
        )
        db.add(copy)
        await db.commit()
        copy_id = copy.id
    async with SessionLocal() as db:
        await decisions.merge_identities(db, flow.user.id, copy_id, flow.game_id)
        await db.commit()
    headers, _ = await grant_access(flow, ["games.read"])
    library = (await flow.client.get(BASE + "/games", headers=headers)).json()["games"]
    identity = {row["id"]: row["identity"] for row in library}
    assert identity[str(copy_id)] == {
        "group_id": str(flow.game_id),
        "is_main": False,
        "main_title": "Private game 0",
    }
    assert identity[str(flow.game_id)]["is_main"] is True


async def test_named_note_sync_requires_explicit_scopes_and_preserves_concurrent_edits(
    delegation_flow, monkeypatch, tmp_path
):
    from src.api.routes import game_notes as note_routes
    from src.api.routes.utils import games as game_helpers

    monkeypatch.setattr(note_routes, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(game_helpers, "_DATA_ROOT", tmp_path)
    flow = delegation_flow
    endpoint = f"/api/game/{flow.game_id}/notes/Playnite"
    old_headers, _ = await grant_access(flow, ["library.sync"])
    denied = await flow.client.post(endpoint, headers=old_headers, json={"content": "old grant"})
    assert denied.status_code == 403

    headers, _ = await grant_access(flow, ["notes.read", "notes.write"])
    original = "A shared note\r\n日本語 🎮"
    created = await flow.client.post(endpoint, headers=headers, json={"content": original})
    assert created.status_code == 201, created.text
    digest = hashlib.sha256(original.encode()).hexdigest()
    assert created.json()["content_sha256"] == digest
    fetched = await flow.client.get(endpoint, headers=headers)
    assert fetched.status_code == 200 and fetched.text == original
    assert fetched.headers["etag"] == '"' + digest + '"'

    # Both writers observed one revision. Only one may replace it; the other
    # retains its local text for a conflict copy instead of losing the first edit.
    results = await asyncio.gather(
        *(
            flow.client.put(
                endpoint, headers=headers, json={"content": value, "expected_sha256": digest}
            )
            for value in ("Edited in app", "Edited in launcher")
        )
    )
    assert sorted(result.status_code for result in results) == [200, 409]
    current = await flow.client.get(endpoint, headers=headers)
    assert current.text in {"Edited in app", "Edited in launcher"}
    assert current.headers["etag"] == '"' + hashlib.sha256(current.content).hexdigest() + '"'
    refused = next(result for result in results if result.status_code == 409)
    assert refused.json()["detail"]["error"] == "note_changed"

    for method, suffix in (("GET", ""), ("POST", "")):
        foreign = f"/api/game/{flow.other_game_id}/notes/Playnite{suffix}"
        response = await flow.client.request(
            method,
            foreign,
            headers=headers,
            json={"content": "foreign"} if method == "POST" else None,
        )
        assert response.status_code == 404
    # The new scopes do not expand old credentials, delete notes or expose history.
    assert (
        await flow.client.post(endpoint, headers=old_headers, json={"content": "still denied"})
    ).status_code == 403
    assert (await flow.client.delete(endpoint, headers=headers)).status_code == 403
    assert (await flow.client.get(endpoint + "/versions", headers=headers)).status_code == 403


async def test_note_read_and_write_capabilities_are_independent(
    delegation_flow, monkeypatch, tmp_path
):
    from src.api.routes import game_notes as note_routes
    from src.api.routes.utils import games as game_helpers

    monkeypatch.setattr(note_routes, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(game_helpers, "_DATA_ROOT", tmp_path)
    flow = delegation_flow
    endpoint = f"/api/game/{flow.game_id}/notes/Playnite"
    write, _ = await grant_access(flow, ["notes.write"])
    assert (
        await flow.client.post(endpoint, headers=write, json={"content": "keep"})
    ).status_code == 201
    assert (await flow.client.get(endpoint, headers=write)).status_code == 403
    read, _ = await grant_access(flow, ["notes.read"])
    assert (await flow.client.get(endpoint, headers=read)).text == "keep"
    assert (await flow.client.get(f"/api/game/{flow.game_id}/notes", headers=read)).json() == {
        "notes": ["Playnite"]
    }
    assert (
        await flow.client.put(endpoint, headers=read, json={"content": "denied"})
    ).status_code == 403
