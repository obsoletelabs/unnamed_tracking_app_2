"""Real PostgreSQL/HTTP regression coverage for the remote launch trust boundary."""

import asyncio
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from src.api.routes.delegation import browser_user
from src.core.auth import create_api_key
from src.database.models.auth import UserApiKey
from src.database.models.delegation import (
    DelegatedGrant,
    DelegationAudit,
    DelegationRequest,
    IntegrationClient,
    IntegrationPolicy,
)
from src.database.models.game import Game, GameStatus
from src.database.models.remote_launch import RemoteLaunchDevice, RemoteLaunchRequest
from src.database.session import SessionLocal
from src.features import remote_launch
from src.main import app
from tests.test_delegation import grant_access
from tests.test_desktop_app_access import connect, desktop  # noqa: F401

BASE = "/api/playnite/launch/v1"


@pytest.fixture
async def launch_flow(desktop):  # noqa: F811 - imported shared pytest fixture
    flow = desktop
    flow.headers = await connect(flow, scopes=["library.sync", "games.launch"])
    grants = (await flow.client.get("/api/delegation/v1/grants")).json()
    flow.device_id = UUID(grants[0]["id"])
    async with SessionLocal() as db:
        grant = await db.get(DelegatedGrant, flow.device_id)
        flow.integration_id = grant.client_id
        game = await db.get(Game, flow.game_id)
        game.playnite_guid = uuid4()
        flow.playnite_id = str(game.playnite_guid)
        await db.commit()
    response = await flow.client.put(BASE + "/device", headers=flow.headers, json={"enabled": True})
    assert response.status_code == 200, response.text
    yield flow
    async with SessionLocal() as db:
        client = await db.get(IntegrationClient, flow.integration_id)
        client.kind, client.enabled = "desktop", True
        await db.commit()


def launch_body(flow, **changes):
    return {
        "device_id": str(flow.device_id),
        "game_id": str(flow.game_id),
        "request_key": str(uuid4()),
        **changes,
    }


async def queued(flow):
    response = await flow.client.post(BASE + "/requests", json=launch_body(flow))
    assert response.status_code == 200, response.text
    return response.json()


async def claim(flow, headers=None):
    return await flow.client.post(BASE + "/requests/claim", headers=headers or flow.headers)


async def test_known_game_is_delivered_once_and_result_is_idempotent(launch_flow):
    flow = launch_flow
    devices = await flow.client.get(BASE + "/devices")
    assert devices.headers["cache-control"] == "no-store"
    assert devices.json()[0]["id"] == str(flow.device_id)
    assert devices.json()[0]["online"] is True
    request = await queued(flow)
    assert request["state"] == "pending"
    responses = await asyncio.gather(claim(flow), claim(flow))
    for response in responses:
        assert request["created_at"] <= response.json()["server_time"] <= int(time.time())
    deliveries = [r.json()["request"] for r in responses if r.json()["request"]]
    assert deliveries == [
        {
            "id": request["id"],
            "playnite_id": flow.playnite_id,
            "expires_at": request["expires_at"],
        }
    ]
    endpoint = BASE + "/requests/" + request["id"] + "/result"
    results = await asyncio.gather(
        *[
            flow.client.post(endpoint, headers=flow.headers, json={"state": "accepted"})
            for _ in range(2)
        ]
    )
    assert [r.status_code for r in results] == [200, 200]
    assert (await flow.client.get(BASE + "/requests/" + request["id"])).json()[
        "state"
    ] == "accepted"
    assert (await claim(flow)).json()["request"] is None
    async with SessionLocal() as db:
        events = list(
            await db.scalars(
                select(DelegationAudit.event).where(
                    DelegationAudit.grant_id == flow.device_id,
                    DelegationAudit.event.like("launch.%"),
                )
            )
        )
    assert sorted(events) == sorted(
        [
            "launch.enabled",
            "launch.queued",
            "launch.dispatched",
            "launch.accepted",
        ]
    )


async def test_browser_retries_share_one_request_and_cannot_change_its_target(launch_flow):
    flow = launch_flow
    body = launch_body(flow)
    replies = await asyncio.gather(
        *[flow.client.post(BASE + "/requests", json=body) for _ in range(2)]
    )
    assert all(r.status_code == 200 for r in replies)
    assert replies[0].json()["id"] == replies[1].json()["id"]
    response = await flow.client.post(BASE + "/requests", json={**body, "game_id": str(uuid4())})
    assert response.status_code == 409
    assert (await flow.client.post(BASE + "/requests", json=launch_body(flow))).status_code == 409


@pytest.mark.parametrize(
    "field,value",
    [
        ("command", "calc.exe"),
        ("path", "C:/Games/game.exe"),
        ("uri", "steam://run/620"),
        ("playnite_id", str(uuid4())),
    ],
)
async def test_commands_paths_and_caller_selected_launcher_ids_are_rejected(
    launch_flow, field, value
):
    flow = launch_flow
    response = await flow.client.post(BASE + "/requests", json=launch_body(flow, **{field: value}))
    assert response.status_code == 422


async def test_only_browser_sessions_queue_and_only_delegated_receivers_poll(launch_flow):
    flow = launch_flow
    app.dependency_overrides.pop(browser_user)
    assert (await flow.client.get(BASE + "/devices")).status_code == 401
    assert (await flow.client.post(BASE + "/requests", json=launch_body(flow))).status_code == 401
    assert (await flow.client.post(BASE + "/requests/claim")).status_code == 401
    raw, prefix, digest = create_api_key()
    async with SessionLocal() as db:
        db.add(
            UserApiKey(
                user_id=flow.user.id,
                name="legacy",
                key_prefix=prefix,
                key_hash=digest,
                scopes=["read", "write", "admin"],
            )
        )
        await db.commit()
    headers = {"Authorization": "Bearer " + raw}
    assert (await flow.client.get(BASE + "/devices", headers=headers)).status_code == 403
    assert (await claim(flow, headers)).status_code == 401
    assert (await flow.client.get(BASE + "/devices", headers=flow.headers)).status_code == 403


async def test_sync_permission_and_web_grants_do_not_authorize_receiving_launches(launch_flow):
    flow = launch_flow
    headers, _ = await grant_access(flow, ["library.sync"])
    assert (await claim(flow, headers)).status_code == 403
    async with SessionLocal() as db:
        client = await db.get(IntegrationClient, flow.integration_id)
        client.kind = "web"
        (await db.get(IntegrationPolicy, 1)).enabled = True
        await db.commit()
    assert (await claim(flow)).status_code == 403


async def test_other_devices_and_users_cannot_claim_read_or_complete_requests(launch_flow):
    flow = launch_flow
    request = await queued(flow)
    headers, second_id = await grant_access(flow, ["games.launch"])
    await flow.client.put(BASE + "/device", headers=headers, json={"enabled": True})
    assert (await claim(flow, headers)).json()["request"] is None
    result_path = BASE + "/requests/" + request["id"] + "/result"
    assert (
        await flow.client.post(result_path, headers=headers, json={"state": "accepted"})
    ).status_code == 404
    app.dependency_overrides[browser_user] = lambda: flow.other
    assert (await flow.client.get(BASE + "/devices")).json() == []
    assert (await flow.client.get(BASE + "/requests/" + request["id"])).status_code == 404
    assert (await flow.client.post(BASE + "/requests", json=launch_body(flow))).status_code == 404
    app.dependency_overrides[browser_user] = lambda: flow.user
    assert (
        await flow.client.post(
            BASE + "/requests",
            json=launch_body(
                flow,
                device_id=str(second_id),
                game_id=str(flow.other_game_id),
            ),
        )
    ).status_code == 404


@pytest.mark.parametrize("change", ["revoked", "scope", "expiry", "client", "policy"])
async def test_queued_launch_does_not_survive_permission_removal(launch_flow, change):
    flow = launch_flow
    request = await queued(flow)
    async with SessionLocal() as db:
        grant = await db.get(DelegatedGrant, flow.device_id)
        if change == "revoked":
            grant.revoked_at = int(time.time())
        elif change == "scope":
            grant.scopes = ["library.sync"]
        elif change == "expiry":
            grant.expires_at = int(time.time()) - 1
        elif change == "client":
            (await db.get(IntegrationClient, flow.integration_id)).enabled = False
        else:
            (await db.get(IntegrationPolicy, 1)).allow_desktop_apps = False
        await db.commit()
    assert (await claim(flow)).status_code in (401, 403)
    assert (await flow.client.get(BASE + "/devices")).json() == []
    status = await flow.client.get(BASE + "/requests/" + request["id"])
    assert status.json()["state"] == "expired"


@pytest.mark.parametrize("dispatched", [False, True])
async def test_expired_or_lost_delivery_is_never_replayed(launch_flow, dispatched):
    flow = launch_flow
    request = await queued(flow)
    if dispatched:
        assert (await claim(flow)).json()["request"]
    async with SessionLocal() as db:
        (await db.get(RemoteLaunchRequest, UUID(request["id"]))).expires_at = int(time.time()) - 1
        await db.commit()
    assert (await claim(flow)).json()["request"] is None
    state = (await flow.client.get(BASE + "/requests/" + request["id"])).json()["state"]
    assert state == ("unknown" if dispatched else "expired")
    late = await flow.client.post(
        BASE + "/requests/" + request["id"] + "/result",
        headers=flow.headers,
        json={"state": "accepted"},
    )
    assert late.status_code == 409


async def test_offline_and_disabled_computers_cannot_accumulate_launches(launch_flow):
    flow = launch_flow
    async with SessionLocal() as db:
        device = await db.get(RemoteLaunchDevice, flow.device_id)
        device.last_seen_at = int(time.time()) - remote_launch.ONLINE_SECONDS
        await db.commit()
    assert (await flow.client.get(BASE + "/devices")).json()[0]["online"] is False
    assert (await flow.client.post(BASE + "/requests", json=launch_body(flow))).status_code == 409
    await claim(flow)
    request = await queued(flow)
    await flow.client.put(BASE + "/device", headers=flow.headers, json={"enabled": False})
    await flow.client.put(BASE + "/device", headers=flow.headers, json={"enabled": True})
    assert (await claim(flow)).json()["request"] is None
    assert (await flow.client.get(BASE + "/requests/" + request["id"])).json()["state"] == "expired"


@pytest.mark.parametrize("change", ["trash", "guid", "wishlist"])
async def test_game_identity_and_availability_are_rechecked_at_dispatch(launch_flow, change):
    flow = launch_flow
    request = await queued(flow)
    async with SessionLocal() as db:
        game = await db.get(Game, flow.game_id)
        if change == "trash":
            game.deleted_at = int(time.time())
        elif change == "guid":
            game.playnite_guid = uuid4()
        else:
            game.status = GameStatus.WISHLIST
        await db.commit()
    assert (await claim(flow)).json()["request"] is None
    status = (await flow.client.get(BASE + "/requests/" + request["id"])).json()
    assert (status["state"], status["failure_code"]) == ("failed", "game_unavailable")


async def test_rate_limit_and_history_retention(launch_flow):
    flow = launch_flow
    for _ in range(6):
        request = await queued(flow)
        await claim(flow)
        result = await flow.client.post(
            BASE + "/requests/" + request["id"] + "/result",
            headers=flow.headers,
            json={"state": "failed", "failure_code": "not_installed"},
        )
        assert result.status_code == 200
    response = await flow.client.post(BASE + "/requests", json=launch_body(flow))
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(RemoteLaunchRequest).where(RemoteLaunchRequest.user_id == flow.user.id)
        )
        for row in rows:
            row.expires_at = int(time.time()) - remote_launch.HISTORY_SECONDS - 1
        await db.commit()
        assert await remote_launch.purge_history(db) == 6
        assert (
            await db.scalar(
                select(func.count())
                .select_from(RemoteLaunchRequest)
                .where(
                    RemoteLaunchRequest.user_id == flow.user.id,
                )
            )
            == 0
        )


async def test_consent_alone_does_not_enable_a_receiver(launch_flow):
    flow = launch_flow
    _headers, grant_id = await grant_access(flow, ["games.launch"])
    response = await flow.client.post(
        BASE + "/requests", json=launch_body(flow, device_id=str(grant_id))
    )
    assert response.status_code == 409
    assert {row["id"] for row in (await flow.client.get(BASE + "/devices")).json()} == {
        str(flow.device_id)
    }


async def test_results_cannot_precede_dispatch_or_change_a_terminal_outcome(launch_flow):
    flow = launch_flow
    request = await queued(flow)
    endpoint = BASE + "/requests/" + request["id"] + "/result"
    assert (
        await flow.client.post(endpoint, headers=flow.headers, json={"state": "accepted"})
    ).status_code == 409
    await claim(flow)
    for payload in (
        {"state": "failed"},
        {"state": "accepted", "failure_code": "not_installed"},
        {"state": "failed", "failure_code": "raw exception"},
        {"state": "running"},
    ):
        assert (
            await flow.client.post(endpoint, headers=flow.headers, json=payload)
        ).status_code == 422
    failure = {"state": "failed", "failure_code": "launch_failed"}
    assert (await flow.client.post(endpoint, headers=flow.headers, json=failure)).status_code == 200
    assert (await flow.client.post(endpoint, headers=flow.headers, json=failure)).status_code == 200
    assert (
        await flow.client.post(endpoint, headers=flow.headers, json={"state": "accepted"})
    ).status_code == 409


async def test_hourly_limit_and_audit_retention(launch_flow):
    flow = launch_flow
    now = int(time.time())
    async with SessionLocal() as db:
        for _ in range(60):
            db.add(
                RemoteLaunchRequest(
                    user_id=flow.user.id,
                    device_id=flow.device_id,
                    game_id=flow.game_id,
                    playnite_id=UUID(flow.playnite_id),
                    request_key=uuid4(),
                    state="accepted",
                    created_at=now - 61,
                    expires_at=now - 1,
                    completed_at=now - 1,
                )
            )
        old = now - remote_launch.HISTORY_SECONDS - 1
        audits = [
            DelegationAudit(
                user_id=flow.user.id,
                client_id=flow.integration_id,
                grant_id=flow.device_id,
                event=event,
                created_at=old,
            )
            for event in ("approved", "launch.accepted")
        ]
        db.add_all(audits)
        await db.commit()
        keep_id, remove_id = [row.id for row in audits]
        assert await remote_launch.purge_history(db) == 0
        assert await db.get(DelegationAudit, keep_id) is not None
        assert await db.get(DelegationAudit, remove_id) is None
    response = await flow.client.post(BASE + "/requests", json=launch_body(flow))
    assert response.status_code == 429


async def test_same_reported_device_identity_still_creates_a_separate_receiver(launch_flow):
    flow = launch_flow
    headers = await connect(flow, scopes=["library.sync", "games.launch"])
    response = await flow.client.put(BASE + "/device", headers=headers, json={"enabled": True})
    assert response.status_code == 200 and response.json()["id"] != str(flow.device_id)
    devices = (await flow.client.get(BASE + "/devices")).json()
    assert len(devices) == 2 and {row["name"] for row in devices} == {"Gaming PC"}
    await queued(flow)
    assert (await claim(flow, headers)).json()["request"] is None


@pytest.mark.parametrize(
    "version", ["0.2.0", "0.3.0", "1.12.3", None, "garbage<script>", "999999.0.0"]
)
async def test_sync_contract_records_only_bounded_display_version(launch_flow, version):
    flow = launch_flow
    headers = dict(flow.headers)
    if version is not None:
        headers["X-Playnite-Extension-Version"] = version
    response = await flow.client.get("/api/playnite/contract", headers=headers)
    assert response.status_code == 200
    async with SessionLocal() as db:
        grant = await db.get(DelegatedGrant, flow.device_id)
        connection = await db.get(DelegationRequest, grant.request_id)
        metadata = connection.external_account
        expected = version if version in {"0.2.0", "0.3.0", "1.12.3"} else None
        assert metadata["playnite_extension_version"] == expected
        assert metadata["playnite_version_checked_at"] > 0
        assert metadata["username"] == "Gaming PC"
    # An unauthenticated caller cannot overwrite a connection's diagnostic report.
    refused = await flow.client.get(
        "/api/playnite/contract", headers={"X-Playnite-Extension-Version": "9.9.9"}
    )
    assert refused.status_code == 401
    async with SessionLocal() as db:
        connection = await db.get(DelegationRequest, grant.request_id)
        assert connection.external_account["playnite_extension_version"] == expected


async def test_reported_extension_version_does_not_grant_remote_launch(desktop):  # noqa: F811
    headers = await connect(desktop, scopes=["library.sync"])
    headers["X-Playnite-Extension-Version"] = "0.3.0"
    assert (await desktop.client.get("/api/playnite/contract", headers=headers)).status_code == 200
    assert (
        await desktop.client.put(BASE + "/device", headers=headers, json={"enabled": True})
    ).status_code == 403
    assert (await desktop.client.get(BASE + "/devices")).json() == []


async def test_version_reports_are_per_pc_and_api_keys_cannot_replace_them(launch_flow):
    flow = launch_flow
    other = await connect(flow, scopes=["library.sync"])
    for headers, version in [(flow.headers, "0.2.0"), (other, "0.3.0")]:
        response = await flow.client.get(
            "/api/playnite/contract", headers={**headers, "X-Playnite-Extension-Version": version}
        )
        assert response.status_code == 200
    raw, prefix, digest = create_api_key()
    async with SessionLocal() as db:
        db.add(
            UserApiKey(
                user_id=flow.user.id,
                name="legacy",
                key_prefix=prefix,
                key_hash=digest,
                scopes=["read", "write", "admin"],
            )
        )
        await db.commit()
    assert (
        await flow.client.get(
            "/api/playnite/contract",
            headers={"Authorization": "Bearer " + raw, "X-Playnite-Extension-Version": "9.9.9"},
        )
    ).status_code == 200
    grants = (await flow.client.get("/api/delegation/v1/grants")).json()
    assert len(grants) == 2
    assert {grant["external_account"]["username"] for grant in grants} == {"Gaming PC"}
    assert {grant["external_account"]["playnite_extension_version"] for grant in grants} == {
        "0.2.0",
        "0.3.0",
    }
