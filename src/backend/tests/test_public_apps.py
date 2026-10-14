"""Public-app isolation, live grants and pinned remote-instance transport."""

import socket
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from src.api.routes import public_apps as public_routes
from src.api.routes.plugin_manager import contributions
from src.core.auth import hash_token
from src.database.models.delegation import IntegrationPolicy
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.public_app import PublicAppActor, PublicAppSession, PublicAppSource
from src.database.session import SessionLocal
from src.features import legacy_integrations, public_app_sources
from src.features.delegation import integration_policy
from src.main import app
from src.plugin_api import outbound, public_apps
from src.plugin_api.capabilities import capability_implies
from src.plugin_api.gateway import dispatch_gateway_request


@pytest.fixture
async def public_flow(monkeypatch):
    plugin_id = "test.public-" + uuid4().hex
    provider_id = "test.identity-" + uuid4().hex
    installation_id, provider_installation = uuid4(), uuid4()
    metadata = {
        "api_contract_version": "1.1.12",
        "enabled": True,
        "compatible": True,
        "status": "running",
        "health": "healthy",
        "version": "0.0.1",
    }
    plugins = [
        {
            **metadata,
            "plugin_id": plugin_id,
            "installation_id": str(installation_id),
            "public_app": {
                "entry": "frontend/index.html",
                "action_id": "session",
                "identity_provider_plugin_id": provider_id,
            },
        },
        {
            **metadata,
            "plugin_id": provider_id,
            "installation_id": str(provider_installation),
            "background_user_id": str(uuid4()),
            "identity_provider": {
                "config_action_id": "config",
                "authenticate_action_id": "verify",
                "revalidate_action_id": "check",
            },
        },
    ]
    calls = []

    async def fake_plugins():
        return plugins

    async def fake_action(_plugin_id, action_id, values, **_options):
        calls.append((action_id, values))
        if action_id == "config":
            return {"configured": True}
        if action_id == "verify":
            return {
                "verified": True,
                "subject": "verified-subject",
                "room": "verified-room",
                "display_name": "Participant",
            }
        return {"verified": True}

    async def fake_public_action(_plugin_id, values, **options):
        return {"context": options["context"], "values": values}

    monkeypatch.setattr(public_apps.client, "plugins", fake_plugins)
    monkeypatch.setattr(public_apps.client, "action", fake_action)
    monkeypatch.setattr(public_apps.client, "public_app_action", fake_public_action)
    async with SessionLocal() as db:
        policy = await integration_policy(db)
        previous = policy.enabled
        policy.enabled = True
        for pid, iid, capability in [
            (plugin_id, installation_id, "integrations.public_app"),
            (provider_id, provider_installation, "integrations.identity_provider"),
            (plugin_id, installation_id, "plugin.storage"),
            (plugin_id, installation_id, "api.full"),
        ]:
            db.add(
                PluginPermissionGrant(
                    plugin_id=pid, installation_id=iid, capability=capability, capability_version=1
                )
            )
        await db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        flow = SimpleNamespace(
            client=client,
            plugin_id=plugin_id,
            base=f"/api/public-apps/{plugin_id}",
            installation_id=installation_id,
            provider_installation=provider_installation,
            provider_id=provider_id,
            calls=calls,
            plugins=plugins,
        )
        yield flow
    async with SessionLocal() as db:
        await db.execute(delete(PublicAppActor).where(PublicAppActor.plugin_id == plugin_id))
        await db.execute(
            delete(PluginPermissionGrant).where(
                PluginPermissionGrant.plugin_id.in_([plugin_id, provider_id])
            )
        )
        policy = await db.get(IntegrationPolicy, 1)
        policy.enabled = previous
        await db.commit()


async def sign_in(flow):
    response = await flow.client.post(
        flow.base + "/sessions", json={"proof": {"opaque": "provider-proof"}}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    return {"Authorization": "Bearer " + result["session_token"]}, UUID(result["participant"]["id"])


@pytest.mark.parametrize(
    "extra_scopes",
    [[], ["achievements.read", "tracking.write", "achievements.write"], ["compatibility.write"]],
)
async def test_source_consent_requests_only_chosen_capabilities(
    public_flow, monkeypatch, extra_scopes
):
    flow = public_flow
    headers, _actor = await sign_in(flow)
    origin, operator = "https://source.example", "https://operator.example"
    monkeypatch.setattr(public_app_sources.settings, "PUBLIC_APP_URL", operator)
    requests = []

    def remote(_origin, path, **options):
        if path.endswith("/instance"):
            return {
                "status": 200,
                "data": {
                    "enabled": True,
                    "protocol_version": "1.0",
                    "instance_id": str(uuid4()),
                    "clients": [{"id": str(uuid4()), "origin": operator}],
                },
            }
        requests.append(options["body"])
        return {
            "status": 201,
            "data": {"request_id": str(uuid4()), "expires_at": int(time.time()) + 600},
        }

    monkeypatch.setattr(public_app_sources, "public_instance_request", remote)
    payload = {"origin": origin}
    if extra_scopes:
        payload["scopes"] = [*public_app_sources.LIBRARY_SCOPES, *extra_scopes]
    response = await flow.client.post(flow.base + "/sources", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    assert requests[0]["scopes"] == sorted([*public_app_sources.LIBRARY_SCOPES, *extra_scopes])
    assert response.json()["scopes"] == []  # A request is not a grant.


@pytest.mark.parametrize(
    "scopes",
    [
        [],
        ["profile.read", "api.full"],
        ["games.read", "games.read"],
        ["games.read", "tracking.write"],
    ],
)
async def test_source_rejects_invalid_or_incomplete_capabilities(public_flow, monkeypatch, scopes):
    flow = public_flow
    headers, _actor = await sign_in(flow)
    calls = []
    monkeypatch.setattr(
        public_app_sources, "public_instance_request", lambda *args, **kwargs: calls.append(args)
    )
    response = await flow.client.post(
        flow.base + "/sources",
        headers=headers,
        json={"origin": "https://source.example", "scopes": scopes},
    )
    assert response.status_code == 422, response.text
    assert calls == []


@pytest.mark.parametrize(
    "avatar",
    [
        "https://[malformed",
        "https://example.test:bad/avatar.png",
        "https://user:pass@example.test/a",
        "http://example.test/a",
        "https:///missing-host",
        "https://example.test/a#fragment",
        123,
    ],
)
async def test_bad_provider_avatar_does_not_break_sign_in(public_flow, monkeypatch, avatar):
    flow = public_flow

    async def verified(*_args, **_kwargs):
        return {
            "verified": True,
            "subject": "avatar-test",
            "room": "verified-room",
            "display_name": "Participant",
            "avatar_url": avatar,
        }

    monkeypatch.setattr(public_apps.client, "action", verified)
    _headers, actor_id = await sign_in(flow)
    async with SessionLocal() as db:
        assert (await db.get(PublicAppActor, actor_id)).avatar_url is None


async def test_avatars_require_a_live_session_in_the_same_room(public_flow, monkeypatch):
    flow = public_flow
    headers, actor_id = await sign_in(flow)
    avatar_url = "https://cdn.example.test/avatar.png"
    async with SessionLocal() as db:
        actor = await db.get(PublicAppActor, actor_id)
        actor.avatar_url = avatar_url
        await db.commit()
    calls = []

    def image(url):
        calls.append(url)
        return {"content": b"\x89PNG\r\n\x1a\nfixture", "media_type": "image/png"}

    monkeypatch.setattr(public_routes, "public_image_request", image)
    path = flow.base + f"/participants/{actor_id}/avatar"
    assert (await flow.client.get(path)).status_code == 401
    result = await flow.client.get(path, headers=headers)
    assert result.status_code == 200 and result.headers["content-type"] == "image/png"
    assert result.headers["cache-control"] == "no-store"
    async with SessionLocal() as db:
        other = PublicAppActor(
            plugin_id=flow.plugin_id,
            installation_id=flow.installation_id,
            provider_installation_id=flow.provider_installation,
            subject="other",
            display_name="Other",
            avatar_url=avatar_url,
            expires_at=int(time.time()) + 600,
        )
        db.add(other)
        await db.flush()
        db.add(
            PublicAppSession(
                actor_id=other.id,
                token_hash=hash_token(uuid4().hex),
                room="other-room",
                expires_at=int(time.time()) + 600,
            )
        )
        await db.commit()
        other_id = other.id
    assert (
        await flow.client.get(flow.base + f"/participants/{other_id}/avatar", headers=headers)
    ).status_code == 404
    assert calls == [avatar_url]


@pytest.mark.parametrize(
    "status,content,expected",
    [
        (200, b"\x89PNG\r\n\x1a\nfixture", "image/png"),
        (200, b"\xff\xd8\xfffixture", "image/jpeg"),
        (200, b"RIFF0000WEBPfixture", "image/webp"),
        (200, b"<svg>active content</svg>", None),
        (302, b"redirect", None),
        (200, b"x" * (2 * 1024 * 1024 + 1), None),
    ],
)
def test_public_images_are_bounded_raster_only_without_redirects(
    monkeypatch, status, content, expected
):
    class Connection:
        closed = False

        def request(self, method, path, headers):
            assert method == "GET" and path == "/cover.png?size=128"
            assert set(headers) == {"Accept"}

        def getresponse(self):
            return SimpleNamespace(status=status, read=lambda maximum: content[:maximum])

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(outbound, "_public_connection", lambda origin: connection)
    if expected:
        assert outbound.public_image_request("https://public.example/cover.png?size=128") == {
            "content": content,
            "media_type": expected,
        }
    else:
        with pytest.raises(ValueError):
            outbound.public_image_request("https://public.example/cover.png?size=128")
    assert connection.closed


@pytest.mark.parametrize("address", ["127.0.0.1", "192.168.0.1", "::1"])
def test_public_images_reject_private_networks(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    with pytest.raises(ValueError, match="public"):
        outbound.public_image_request("https://image.example/cover.png")


async def test_external_context_is_host_owned_and_not_an_app_account(public_flow):
    flow = public_flow
    headers, actor_id = await sign_in(flow)
    response = await flow.client.post(
        flow.base + "/action", headers=headers, json={"values": {"operation": "snapshot"}}
    )
    assert response.status_code == 200
    assert response.json()["context"]["actor_id"] == str(actor_id)
    assert response.json()["context"]["room"] == "verified-room"
    for path in ("/api/auth/me", "/api/auth/users", "/api/plugins"):
        assert (await flow.client.get(path, headers=headers)).status_code == 403
    for reserved in ("_public_app_context", "_plugin_context"):
        assert (
            await flow.client.post(
                flow.base + "/action",
                headers=headers,
                json={"values": {reserved: {"is_admin": True}}},
            )
        ).status_code == 422
    async with SessionLocal() as db:
        assert (
            await db.scalar(select(PublicAppActor.id).where(PublicAppActor.id == actor_id))
            == actor_id
        )
        session = await db.scalar(
            select(PublicAppSession).where(PublicAppSession.actor_id == actor_id)
        )
        assert session.token_hash == hash_token(headers["Authorization"][7:])


async def test_full_api_does_not_grant_external_identity_or_account_access(public_flow):
    flow = public_flow
    assert not capability_implies("api.full", "integrations.public_app")
    assert not capability_implies("api.full", "integrations.identity_provider")
    _headers, actor_id = await sign_in(flow)
    async with SessionLocal() as db:
        assert await dispatch_gateway_request(
            db,
            plugin_id=flow.plugin_id,
            installation_id=flow.installation_id,
            user_id=actor_id,
            method="capabilities.check",
            capability="plugin.storage",
            payload={},
        )
        for method, capability in [
            ("games.list", "games.read"),
            ("network.request", "network.outbound"),
            ("capabilities.check", "api.full"),
        ]:
            with pytest.raises(PermissionError, match="External"):
                await dispatch_gateway_request(
                    db,
                    plugin_id=flow.plugin_id,
                    installation_id=flow.installation_id,
                    user_id=actor_id,
                    method=method,
                    capability=capability,
                    payload={},
                )


async def test_runtime_http_gateway_accepts_only_live_external_principals(public_flow, monkeypatch):
    flow = public_flow
    _headers, actor_id = await sign_in(flow)
    monkeypatch.setenv("PLUGIN_RUNTIME_TOKEN", "test-runtime-token-" + "x" * 32)

    async def live_plugin(_plugin_id, **_options):
        return flow.plugins[0]

    monkeypatch.setattr(contributions.runtime, "live_plugin", live_plugin)
    payload = {
        "plugin_id": flow.plugin_id,
        "installation_id": str(flow.installation_id),
        "user_id": str(actor_id),
        "request_id": str(uuid4()),
        "method": "capabilities.check",
        "capability": "plugin.storage",
        "payload": {},
    }
    headers = {"X-Plugin-Runtime-Token": "test-runtime-token-" + "x" * 32}
    response = await flow.client.post("/api/plugins/runtime/gateway", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    payload.update(method="games.list", capability="games.read")
    assert (
        await flow.client.post("/api/plugins/runtime/gateway", headers=headers, json=payload)
    ).status_code == 403
    payload.update(method="capabilities.check", capability="plugin.storage")
    async with SessionLocal() as db:
        await db.execute(delete(PublicAppSession).where(PublicAppSession.actor_id == actor_id))
        await db.commit()
    assert (
        await flow.client.post("/api/plugins/runtime/gateway", headers=headers, json=payload)
    ).status_code == 403


async def test_revocation_and_installation_replacement_block_existing_session(public_flow):
    flow = public_flow
    headers, _actor_id = await sign_in(flow)
    async with SessionLocal() as db:
        grant = await db.scalar(
            select(PluginPermissionGrant).where(
                PluginPermissionGrant.plugin_id == flow.plugin_id,
                PluginPermissionGrant.capability == "integrations.public_app",
            )
        )
        grant.revoked_at = int(time.time())
        await db.commit()
    assert (await flow.client.get(flow.base + "/sources", headers=headers)).status_code == 403
    async with SessionLocal() as db:
        grant = await db.get(PluginPermissionGrant, grant.id)
        grant.revoked_at = None
        await db.commit()
    flow.plugins[1]["installation_id"] = str(uuid4())
    assert (await flow.client.get(flow.base + "/sources", headers=headers)).status_code in {
        401,
        403,
    }


async def test_connection_cannot_be_selected_by_another_actor(public_flow):
    flow = public_flow
    headers, actor_id = await sign_in(flow)
    async with SessionLocal() as db:
        foreign = PublicAppActor(
            plugin_id=flow.plugin_id,
            installation_id=flow.installation_id,
            provider_installation_id=flow.provider_installation,
            subject="another",
            display_name="Other",
            expires_at=int(time.time()) + 3600,
        )
        db.add(foreign)
        await db.flush()
        source = PublicAppSource(
            actor_id=foreign.id,
            origin="https://example.test",
            instance_id=str(uuid4()),
            request_id=uuid4(),
            expires_at=int(time.time()) + 3600,
        )
        db.add(source)
        await db.commit()
    assert (
        await flow.client.delete(flow.base + f"/sources/{source.id}", headers=headers)
    ).status_code == 404
    assert (
        await flow.client.patch(
            flow.base + f"/sources/{source.id}/games/{uuid4()}/play-compatibility",
            headers=headers,
            json={"play_compatibility": {}},
        )
    ).status_code == 404
    async with SessionLocal() as db:
        actor = await db.get(PublicAppActor, actor_id)
        actor.expires_at = 0
        await db.commit()
        await public_apps.purge_expired_data(db)
        await db.refresh(actor)
        assert actor.subject.startswith("expired:") and actor.display_name == ""


async def test_multiplayer_save_forwards_only_validated_preset(public_flow, monkeypatch):
    flow = public_flow
    headers, actor_id = await sign_in(flow)
    async with SessionLocal() as db:
        source = PublicAppSource(
            actor_id=actor_id, origin="https://source.example", instance_id=str(uuid4())
        )
        db.add(source)
        await db.commit()
    calls = []

    async def remote(row, path, **options):
        calls.append((row.id, path, options))
        return {"status": 200, "data": {"id": "saved"}}

    monkeypatch.setattr(public_app_sources, "source_request", remote)
    game_id = uuid4()
    endpoint = flow.base + f"/sources/{source.id}/games/{game_id}/play-compatibility"
    result = await flow.client.patch(endpoint, headers=headers, json={"play_compatibility": {}})
    assert result.status_code == 200 and result.json() == {"id": "saved"}
    assert calls == [
        (
            source.id,
            f"/api/delegation/v1/games/{game_id}/play-compatibility",
            {
                "method": "PATCH",
                "body": {
                    "play_compatibility": {
                        "min_players": None,
                        "max_players": None,
                        "duration_minutes": None,
                        "crossplay": False,
                    }
                },
            },
        )
    ]
    assert (
        await flow.client.patch(
            endpoint, headers=headers, json={"play_compatibility": {}, "user_id": str(uuid4())}
        )
    ).status_code == 422
    assert len(calls) == 1


async def test_ongoing_link_survives_guest_retention_but_sessions_expire(public_flow):
    flow = public_flow
    _headers, actor_id = await sign_in(flow)
    async with SessionLocal() as db:
        actor = await db.get(PublicAppActor, actor_id)
        actor.expires_at = 0
        source = PublicAppSource(
            actor_id=actor.id,
            origin="https://example.test",
            instance_id=str(uuid4()),
            request_id=uuid4(),
            expires_at=None,
            credential="encrypted-test-credential",
            state="connected",
        )
        db.add(source)
        await db.commit()
        await public_apps.purge_expired_data(db)
        await db.refresh(actor)
        assert actor.subject == "verified-subject"
        assert await db.get(PublicAppSource, source.id) is not None
        source.credential, source.state = None, "revoked"
        await db.commit()
        await public_apps.purge_expired_data(db)
        await db.refresh(actor)
        assert actor.subject.startswith("expired:")
        assert await db.get(PublicAppSource, source.id) is None


async def test_legacy_connection_requires_owner_opt_in_and_acknowledgement(
    public_flow, monkeypatch
):
    flow = public_flow
    headers, actor_id = await sign_in(flow)
    key = "utk_" + "a" * 48
    user_id, game_id = str(uuid4()), str(uuid4())
    revoked = False

    def legacy_read(origin, path, *, credential):
        assert origin == "https://older.example.test" and credential == key
        if revoked:
            return {"status": 401}
        if path == "/api/auth/me":
            return {"status": 200, "data": {"id": user_id, "steamgriddb_api_key": "private"}}
        assert path == "/api/game/list?skip=0&limit=200"
        return {
            "status": 200,
            "data": [
                {
                    "id": game_id,
                    "title": "Together",
                    "status": "BACKLOG",
                    "folder_location": "private-path",
                    "notes": "private-notes",
                }
            ],
        }

    monkeypatch.setattr(legacy_integrations, "legacy_instance_request", legacy_read)
    payload = {
        "origin": "https://older.example.test",
        "api_key": key,
        "acknowledge_unrestricted_key": True,
    }
    async with SessionLocal() as db:
        policy = await integration_policy(db)
        previous = policy.allow_legacy_api_keys
        policy.allow_legacy_api_keys = False
        await db.commit()
    try:
        assert (
            await flow.client.post(flow.base + "/sources/legacy", headers=headers, json=payload)
        ).status_code == 403
        async with SessionLocal() as db:
            (await db.get(IntegrationPolicy, 1)).allow_legacy_api_keys = True
            await db.commit()
        assert (
            await flow.client.post(
                flow.base + "/sources/legacy",
                headers=headers,
                json={**payload, "acknowledge_unrestricted_key": False},
            )
        ).status_code == 422
        response = await flow.client.post(
            flow.base + "/sources/legacy", headers=headers, json=payload
        )
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["credential_kind"] == "legacy_api_key" and result["expires_at"] is None
        assert key not in response.text
        source_id = UUID(result["id"])
        async with SessionLocal() as db:
            source = await db.get(PublicAppSource, source_id)
            assert source.actor_id == actor_id and source.credential != key
            assert "private-path" not in str(source.library) and "private-notes" not in str(
                source.library
            )
            assert "private" not in str(source.library)
        action = await flow.client.post(flow.base + "/action", headers=headers, json={"values": {}})
        assert key not in action.text
        assert action.json()["context"]["library"][0]["id"] == game_id
        endpoint = flow.base + f"/sources/{source_id}/games/{game_id}/tracking"
        assert (
            await flow.client.patch(endpoint, headers=headers, json={"favorite": True})
        ).status_code == 409
        assert (
            await flow.client.patch(
                flow.base + f"/sources/{source_id}/games/{game_id}/play-compatibility",
                headers=headers,
                json={"play_compatibility": {}},
            )
        ).status_code == 409
        revoked = True
        refresh = await flow.client.post(
            flow.base + f"/sources/{source_id}/refresh", headers=headers
        )
        assert refresh.json()["state"] == "revoked" and refresh.json()["game_count"] == 0
    finally:
        async with SessionLocal() as db:
            (await db.get(IntegrationPolicy, 1)).allow_legacy_api_keys = previous
            await db.commit()


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/users",
        "/api/game/list?skip=0&limit=5000",
        "/api/auth/me?admin=true",
        "/api/delegation/v1/profile",
    ],
)
def test_legacy_transport_permits_only_fixed_read_operations(path):
    with pytest.raises(ValueError, match="legacy instance operation"):
        outbound.legacy_instance_request("https://example.test", path, credential="utk_" + "a" * 48)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "192.168.0.1", "169.254.169.254", "::1", "224.0.0.1"]
)
def test_source_transport_rejects_nonpublic_dns(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    with pytest.raises(ValueError, match="public"):
        outbound.public_instance_request("https://instance.example", "/api/delegation/v1/instance")


def test_source_transport_pins_validated_ip_and_does_not_follow_redirect(monkeypatch):
    connected = []

    class FakeSocket:
        def settimeout(self, value):
            assert value == 8

        def connect(self, address):
            connected.append(address)

        def close(self):
            pass

    class FakeConnection:
        def __init__(self, hostname, port, **options):
            assert hostname == "instance.example" and port == 443
            assert options["context"].check_hostname

        def request(self, method, path, **options):
            self._create_connection(("rebound.private", 443))
            assert path == "/api/delegation/v1/instance"

        def getresponse(self):
            return SimpleNamespace(status=302, read=lambda maximum: b"redirect")

        def close(self):
            pass

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr(outbound, "HTTPSConnection", FakeConnection)
    assert (
        outbound.public_instance_request("https://instance.example", "/api/delegation/v1/instance")[
            "status"
        ]
        == 302
    )
    assert connected == [("8.8.8.8", 443)]
