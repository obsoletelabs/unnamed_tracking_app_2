"""Session/PWA routing and actual encrypted Web Push HTTP behavior on an owned fixture."""

import base64
import json
import socket
import ssl
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import aiohttp
import http_ece
import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from src.api.routes import deployment_settings, notification_providers, plugin_permissions
from src.core.app_integrations import get_or_create_app_integration_settings
from src.core.auth import create_api_key, hash_token, session_cookie_name
from src.core.crypto import encrypt_secret
from src.core.preferences import save_preferences
from src.core.session_manager import create_session
from src.database.models.auth import UserApiKey, UserSession
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal, get_db
from src.features.notification_browser import (
    BrowserSubscription,
    browser_context,
    expire_browser_sessions,
)
from src.features.notification_controller import emit_legacy_rows
from src.features.notification_policy import Trust, effective_trust
from src.features.notification_providers import browser
from src.features.notification_providers.delivery import (
    _claim,
    _dispatch,
    process_pending_deliveries,
)
from src.features.notification_push_config import generate_vapid_key, vapid_key
from src.features.notification_settings import routing_settings
from src.plugin_api import pwa
from src.plugin_api.runtime_client import PluginRuntimeUnavailable


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def subscription(private=None):
    private = private or ec.generate_private_key(ec.SECP256R1())
    return {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test-endpoint-secret",
        "expirationTime": None,
        "keys": {
            "p256dh": b64(
                private.public_key().public_bytes(
                    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
                )
            ),
            "auth": b64(b"owned-test-token"),
        },
    }


@pytest.fixture
async def push_account(monkeypatch):
    user_id, installation_id = uuid4(), uuid4()
    token = uuid4().hex
    private = ec.generate_private_key(ec.SECP256R1())
    plugin_id = "test.notification-pwa"
    live = {
        "plugin_id": plugin_id,
        "installation_id": str(installation_id),
        "enabled": True,
        "compatible": True,
        "api_contract_version": "1.1.2",
        "status": "running",
        "health": "healthy",
        "version": "1.0.0",
        "digest": "1" * 64,
        "pwa": {"name": "Tracking", "short_name": "Tracking"},
    }

    async def plugins():
        return [live]

    monkeypatch.setattr(pwa.client, "plugins", plugins)
    async with SessionLocal() as db:
        app_settings = await get_or_create_app_integration_settings(db)
        saved = (app_settings.web_push_vapid_subject, app_settings.web_push_vapid_private_key)
        app_settings.web_push_vapid_subject = "mailto:push-admin@example.test"
        app_settings.web_push_vapid_private_key = encrypt_secret(generate_vapid_key())
        user = User(
            id=user_id,
            username=user_id.hex,
            email=f"{user_id}@example.test",
            password_hash="unused",
        )
        db.add(user)
        await db.flush()
        session = UserSession(
            user_id=user_id, token_hash=hash_token(token), expires_at=int(time.time()) + 3600
        )
        grant = PluginPermissionGrant(
            plugin_id=plugin_id,
            installation_id=installation_id,
            capability="frontend.pwa",
            capability_version=1,
        )
        db.add_all([session, grant])
        await db.commit()
        context = await browser_context(db)
        assert context is not None
        fixture = SimpleNamespace(
            user_id=user_id,
            session_id=session.id,
            grant_id=grant.id,
            token=token,
            context=context,
            live=live,
            private=private,
            subscription=subscription(private),
            plugin_id=plugin_id,
        )
    try:
        yield fixture
    finally:
        async with SessionLocal() as db:
            row = await get_or_create_app_integration_settings(db)
            row.web_push_vapid_subject, row.web_push_vapid_private_key = saved
            await db.execute(
                delete(PluginPermissionGrant).where(PluginPermissionGrant.id == fixture.grant_id)
            )
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


def api(db, account, *, token=None):
    application = FastAPI()
    application.include_router(notification_providers.router)
    application.include_router(plugin_permissions.router)
    application.include_router(deployment_settings.router)
    application.dependency_overrides[get_db] = lambda: db
    # Authentication is real: no identity override can accidentally bless API keys.
    return AsyncClient(
        transport=ASGITransport(app=application),
        base_url="https://app.example.test",
        cookies={session_cookie_name("app.example.test"): token or account.token},
    )


async def enroll(client, account, **extra):
    response = await client.post(
        "/api/settings/notification-providers/browser-destinations",
        json={
            "subscription": account.subscription,
            "public_key": account.context.configuration.public_key,
            "label": "My browser",
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def queue_notice(db, account, kind="plugin"):
    identifiers = await emit_legacy_rows(
        db,
        account.user_id,
        [
            {
                "kind": kind,
                "media_type": "system",
                "media_id": uuid4(),
                "title": "Private title",
                "body": "never-send-private-body-or-reset-secret",
                "event_at": int(time.time()),
                "dedupe_key": uuid4().hex,
            }
        ],
    )
    await db.commit()
    return identifiers[0]


@pytest.mark.parametrize(
    "url",
    [
        "http://fcm.googleapis.com/x",
        "https://127.0.0.1/x",
        "https://fcm.googleapis.com.evil.test/x",
        "https://fcm.googleapis.com@evil.test/x",
        "https://fcm.googleapis.com:444/x",
        "https://updates.push.services.mozilla.com/x#fragment",
        "file:///etc/passwd",
        "https://fcm.googleapis.com/x\r\nheader:bad",
        "https://evil.notify.windows.com.evil.test/x",
    ],
)
def test_subscription_rejects_arbitrary_endpoints_and_authority_tricks(url):
    with pytest.raises(ValueError):
        BrowserSubscription.model_validate({**subscription(), "endpoint": url})


@pytest.mark.parametrize(
    "host",
    [
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "web.push.apple.com",
        "wns2-am3p.notify.windows.com",
    ],
)
def test_reviewed_browser_service_endpoints(host):
    BrowserSubscription.model_validate(
        {**subscription(), "endpoint": f"https://{host}/opaque-token"}
    )


async def test_owner_enrollment_is_private_write_only_and_idempotent(push_account):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        assert await enroll(client, push_account) == endpoint_id
        endpoint = await db.get(NotificationDestination, endpoint_id)
        assert effective_trust(endpoint) == Trust.PRIVATE
        assert not endpoint.recovery_allowed and endpoint.verified_revision is None
        assert endpoint.configuration_ref == str(push_account.session_id)
        assert "test-endpoint-secret" not in endpoint.encrypted_configuration
        response = await client.get("/api/settings/notification-providers/destinations")
        assert response.status_code == 200, response.text
        assert (
            "test-endpoint-secret" not in response.text
            and push_account.subscription["keys"]["auth"] not in response.text
        )
        meta = next(x for x in response.json()["destinations"] if x["id"] == str(endpoint_id))
        assert meta["available"] and meta["trust"] == "PRIVATE" and not meta["critical_supported"]
        assert not meta["verification_available"]
        path = f"/api/settings/notification-providers/browser-destinations/{endpoint_id}/status?revision=1"
        response = await client.get(path)
        assert response.json() == {"enabled": True, "generation": push_account.context.generation}
        assert response.headers["Cache-Control"] == "no-store"
        assert not (await client.get(path.replace("revision=1", "revision=2"))).json()["enabled"]


async def test_api_key_cannot_enroll_or_authorize_worker_display(push_account):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        key, prefix, hashed = create_api_key()
        db.add(
            UserApiKey(
                user_id=push_account.user_id,
                name="Test",
                key_prefix=prefix,
                key_hash=hashed,
                scopes=[],
            )
        )
        await db.commit()
        client.headers["Authorization"] = f"Bearer {key}"
        response = await client.post(
            "/api/settings/notification-providers/browser-destinations",
            json={
                "subscription": push_account.subscription,
                "public_key": push_account.context.configuration.public_key,
            },
        )
        assert response.status_code == 403
        assert not (
            await client.get(
                f"/api/settings/notification-providers/browser-destinations/{endpoint_id}/status?revision=1"
            )
        ).json()["enabled"]


async def test_same_owner_other_session_cannot_display_and_expiry_retires_work(push_account):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        notice_id = await queue_notice(db, push_account)
        other_token = uuid4().hex
        db.add(
            UserSession(
                user_id=push_account.user_id,
                token_hash=hash_token(other_token),
                expires_at=int(time.time()) + 3600,
            )
        )
        await db.commit()
        async with api(db, push_account, token=other_token) as switched:
            assert not (
                await switched.get(
                    f"/api/settings/notification-providers/browser-destinations/{endpoint_id}/status?revision=1"
                )
            ).json()["enabled"]
        session = await db.get(UserSession, push_account.session_id)
        session.revoked_at = int(time.time())
        await db.commit()
        assert await expire_browser_sessions(db) == 1
        await db.commit()
        endpoint = await db.get(NotificationDestination, endpoint_id, populate_existing=True)
        assert not endpoint.active and endpoint.encrypted_configuration is None
        delivery = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice_id,
                NotificationDelivery.destination_id == endpoint_id,
            )
        )
        assert delivery.status == "suppressed" and delivery.last_error == "browser_session_expired"


async def test_pwa_grant_revocation_retires_destinations_without_replay(push_account):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        user = await db.get(User, push_account.user_id)
        user.is_admin = True
        await db.commit()
        notice_id = await queue_notice(db, push_account)
        response = await client.post(
            f"/api/plugin-permissions/grants/{push_account.grant_id}/revoke"
        )
        assert response.status_code == 200, response.text
        endpoint = await db.get(NotificationDestination, endpoint_id, populate_existing=True)
        assert not endpoint.active and endpoint.encrypted_configuration is None
        delivery = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice_id,
                NotificationDelivery.destination_id == endpoint_id,
            )
        )
        assert delivery.status == "suppressed"


async def test_recovery_and_security_cannot_reach_browser_even_with_forged_proof(push_account):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        endpoint = await db.get(NotificationDestination, endpoint_id)
        endpoint.verified_revision = 1
        endpoint.verification_method = "forged"
        endpoint.recovery_allowed = True
        await db.commit()
        notice_id = await queue_notice(db, push_account, "session_anomaly")
        assert (
            await db.scalar(
                select(NotificationDelivery.id).where(
                    NotificationDelivery.notification_id == notice_id,
                    NotificationDelivery.destination_id == endpoint_id,
                )
            )
            is None
        )
        metadata = await routing_settings(db, push_account.user_id)
        target = next(x for x in metadata["destinations"] if x["id"] == str(endpoint_id))
        assert "auth.session.anomaly" not in target["eligible_types"]


@pytest.fixture
async def push_http(tmp_path, monkeypatch):
    """Real TLS/HTTP; only DNS and trust point to a disposable local push service."""
    captured = []
    outcome = SimpleNamespace(status=201, body=b"", redirect=None)
    certificate_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fcm.googleapis.com")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(certificate_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("fcm.googleapis.com")]), critical=False
        )
        .sign(certificate_key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        certificate_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ssl.load_cert_chain(cert_path, key_path)
    client_ssl = ssl.create_default_context(cafile=str(cert_path))

    async def receive(request):
        captured.append(
            {"path": request.path, "headers": request.headers.copy(), "body": await request.read()}
        )
        return web.Response(
            status=outcome.status,
            body=outcome.body,
            headers={"Location": outcome.redirect} if outcome.redirect else None,
        )

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", receive)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ssl).start()
    port = runner.addresses[0][1]

    class LocalPushResolver(aiohttp.abc.AbstractResolver):
        async def resolve(self, host, port=0, family=socket.AF_INET):
            assert host == "fcm.googleapis.com"
            return [
                {
                    "hostname": host,
                    "host": "127.0.0.1",
                    "port": runner.addresses[0][1],
                    "family": socket.AF_INET,
                    "proto": 0,
                    "flags": 0,
                }
            ]

        async def close(self):
            pass

    session_class = aiohttp.ClientSession

    def session(**kwargs):
        assert kwargs["trust_env"] is False
        return session_class(
            **kwargs, connector=aiohttp.TCPConnector(resolver=LocalPushResolver(), ssl=client_ssl)
        )

    monkeypatch.setattr(browser.aiohttp, "ClientSession", session)
    try:
        yield SimpleNamespace(captured=captured, outcome=outcome, port=port)
    finally:
        await runner.cleanup()


async def test_core_dispatch_encrypts_only_generic_envelope_and_valid_vapid(
    push_account, push_http
):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        notice_id = await queue_notice(db, push_account)
        assert await process_pending_deliveries(db) == 1
        captured = push_http.captured[0]
        decoded = http_ece.decrypt(
            captured["body"],
            private_key=push_account.private,
            auth_secret=b"owned-test-token",
            version="aes128gcm",
        )
        assert json.loads(decoded) == {
            "version": 1,
            "destination": str(endpoint_id),
            "revision": 1,
            "generation": push_account.context.generation,
        }
        assert b"Private" not in decoded and b"reset-secret" not in decoded
        assert str(push_account.user_id).encode() not in decoded
        token = captured["headers"]["Authorization"].split("t=", 1)[1].split(",", 1)[0]
        header, payload, signature = token.split(".")
        raw_signature = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        assert len(raw_signature) == 64
        vapid_key(push_account.context.configuration.private_key).public_key().verify(
            utils.encode_dss_signature(
                int.from_bytes(raw_signature[:32]), int.from_bytes(raw_signature[32:])
            ),
            f"{header}.{payload}".encode(),
            ec.ECDSA(hashes.SHA256()),
        )
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        assert claims["aud"] == "https://fcm.googleapis.com" and claims["exp"] > time.time()
        assert claims["sub"] == "mailto:push-admin@example.test"
        assert captured["headers"]["TTL"] == "60" and captured["headers"]["Urgency"] == "normal"
        work = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice_id,
                NotificationDelivery.destination_id == endpoint_id,
            )
        )
        assert work.status == "sent" and (await db.get(Notification, notice_id)).body.endswith(
            "reset-secret"
        )


@pytest.mark.parametrize(
    "status,retryable,error",
    [
        (429, True, "push_rejected"),
        (503, True, "push_rejected"),
        (400, False, "push_rejected"),
        (410, False, "push_subscription_expired"),
    ],
)
async def test_http_failure_classification_uses_no_remote_error_text(
    push_account, push_http, status, retryable, error
):
    push_http.outcome.status = status
    push_http.outcome.body = b"token-and-private-remote-error"
    result = await browser.send_push(
        BrowserSubscription.model_validate(push_account.subscription),
        push_account.context,
        {"version": 1},
    )
    assert not result.success and result.retryable == retryable and result.error == error


async def test_redirect_is_rejected_before_following_even_same_service(push_account, push_http):
    push_http.outcome.status = 307
    push_http.outcome.redirect = "https://fcm.googleapis.com/stolen"
    result = await browser.send_push(
        BrowserSubscription.model_validate(push_account.subscription),
        push_account.context,
        {"version": 1},
    )
    assert not result.success and result.error == "push_response_rejected"
    assert [x["path"] for x in push_http.captured] == ["/fcm/send/test-endpoint-secret"]


async def test_response_is_bounded_before_library_reads_error_text(push_account, push_http):
    push_http.outcome.body = b"x" * 5000
    result = await browser.send_push(
        BrowserSubscription.model_validate(push_account.subscription),
        push_account.context,
        {"version": 1},
    )
    assert not result.success and result.error == "push_response_rejected"


async def test_expired_service_subscription_retires_credentials_and_unsent_work(
    push_account, push_http
):
    push_http.outcome.status = 410
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        await queue_notice(db, push_account)
        assert await process_pending_deliveries(db) == 0
        endpoint = await db.get(NotificationDestination, endpoint_id, populate_existing=True)
        assert not endpoint.active and endpoint.encrypted_configuration is None


async def test_pwa_outage_defers_without_spending_attempt(push_account, monkeypatch):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        notice_id = await queue_notice(db, push_account)

        async def unavailable():
            raise PluginRuntimeUnavailable("not logged")

        monkeypatch.setattr(pwa.client, "plugins", unavailable)
        assert await process_pending_deliveries(db) == 0
        work = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice_id,
                NotificationDelivery.destination_id == endpoint_id,
            )
        )
        assert work.status == "retry_wait" and work.attempts == 0


async def test_explicit_account_switch_enrollment_retires_prior_owner_without_exposing_it(
    push_account,
):
    other_id, other_token = uuid4(), uuid4().hex
    async with SessionLocal() as db, api(db, push_account) as client:
        original_id = await enroll(client, push_account)
        notice_id = await queue_notice(db, push_account)
        db.add(
            User(
                id=other_id,
                username=other_id.hex,
                email=f"{other_id}@example.test",
                password_hash="unused",
            )
        )
        await db.flush()
        db.add(
            UserSession(
                user_id=other_id,
                token_hash=hash_token(other_token),
                expires_at=int(time.time()) + 3600,
            )
        )
        await db.commit()
        try:
            async with api(db, push_account, token=other_token) as switched:
                response = await switched.get(
                    f"/api/settings/notification-providers/browser-destinations/{original_id}/status?revision=1"
                )
                assert response.json() == {"enabled": False, "generation": None}
                assert (
                    await switched.delete(
                        f"/api/settings/notification-providers/browser-destinations/{original_id}"
                    )
                ).status_code == 404
                replacement_id = await enroll(switched, push_account)
                assert replacement_id != original_id
            original = await db.get(NotificationDestination, original_id, populate_existing=True)
            assert not original.active and original.encrypted_configuration is None
            assert (await db.get(NotificationDestination, replacement_id)).user_id == other_id
            work = await db.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == notice_id,
                    NotificationDelivery.destination_id == original_id,
                )
            )
            assert work.status == "suppressed"
        finally:
            await db.execute(delete(User).where(User.id == other_id))
            await db.commit()


async def test_new_signin_with_previous_cookie_withdraws_push_but_retains_auth_history(
    push_account,
):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        user = await db.get(User, push_account.user_id)
        request = Request(
            {
                "type": "http",
                "headers": [
                    (b"host", b"app.example.test"),
                    (
                        b"cookie",
                        f"{session_cookie_name('app.example.test')}={push_account.token}".encode(),
                    ),
                ],
                "path": "/api/auth/login",
                "scheme": "https",
            }
        )
        created = await create_session(db, user, request)
        await db.commit()
        assert created.session.id != push_account.session_id
        endpoint = await db.get(NotificationDestination, endpoint_id, populate_existing=True)
        assert not endpoint.active and endpoint.encrypted_configuration is None
        assert (await db.get(UserSession, push_account.session_id)).revoked_at is None


async def test_disabled_preferences_and_deleted_claim_never_reach_transport(
    push_account, push_http
):
    from src.features.notification_lifecycle import delete_notice

    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        notice_id = await queue_notice(db, push_account)
        claim = await _claim(db)
        assert claim is not None
        await delete_notice(db, push_account.user_id, notice_id)
        await db.commit()
        assert not await _dispatch(db, *claim)
        assert not push_http.captured
        await save_preferences(
            db, push_account.user_id, {"notification_destinations": {str(endpoint_id): False}}
        )
        await db.commit()
        path = f"/api/settings/notification-providers/browser-destinations/{endpoint_id}/status?revision=1"
        assert not (await client.get(path)).json()["enabled"]
        second = await queue_notice(db, push_account)
        assert (
            await db.scalar(
                select(NotificationDelivery.id).where(
                    NotificationDelivery.notification_id == second,
                    NotificationDelivery.destination_id == endpoint_id,
                )
            )
            is None
        )


async def test_rotation_retires_old_key_but_repeating_same_key_preserves_enrollment(push_account):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        user = await db.get(User, push_account.user_id)
        user.is_admin = True
        await db.commit()
        response = await client.put(
            "/api/settings/deployment",
            json={"web_push_vapid_private_key": push_account.context.configuration.private_key},
        )
        assert response.status_code == 200, response.text
        endpoint = await db.get(NotificationDestination, endpoint_id, populate_existing=True)
        assert endpoint.active
        response = await client.put(
            "/api/settings/deployment", json={"web_push_vapid_private_key": generate_vapid_key()}
        )
        assert response.status_code == 200, response.text
        await db.refresh(endpoint)
        assert not endpoint.active
        assert (
            await client.patch(
                f"/api/settings/notification-providers/browser-destinations/{endpoint_id}",
                json={"enabled": True},
            )
        ).status_code == 404


async def test_browser_normalizes_critical_urgency_and_core_retry_recovers(push_account, push_http):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        notice_id = await queue_notice(db, push_account)
        notice = await db.get(Notification, notice_id)
        await save_preferences(
            db,
            push_account.user_id,
            {
                "notification_routes": {
                    notice.event_type: {
                        str(endpoint_id): {"enabled": True, "urgency": "critical"},
                    }
                }
            },
        )
        push_http.outcome.status = 429
        await db.commit()
        assert await process_pending_deliveries(db) == 0
        work = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice_id,
                NotificationDelivery.destination_id == endpoint_id,
            )
        )
        assert work.status == "retry_wait" and work.attempts == 1
        assert work.requested_urgency == "critical" and work.effective_urgency == "normal"
        work.next_attempt_at = 0
        await db.commit()
        push_http.outcome.status = 201
        assert await process_pending_deliveries(db) == 1
        await db.refresh(work)
        assert work.status == "sent" and work.attempts == 2


async def test_enrollment_rejects_forged_trust_expiry_and_more_than_twenty_devices(push_account):
    async with SessionLocal() as db, api(db, push_account) as client:
        payload = {
            "subscription": push_account.subscription,
            "public_key": push_account.context.configuration.public_key,
        }
        response = await client.post(
            "/api/settings/notification-providers/browser-destinations",
            json={**payload, "privacy": 2},
        )
        assert response.status_code == 422
        response = await client.post(
            "/api/settings/notification-providers/browser-destinations",
            json={
                **payload,
                "subscription": {
                    **push_account.subscription,
                    "expirationTime": int(time.time() * 1000) - 1,
                },
            },
        )
        assert response.status_code == 400
        for index in range(20):
            response = await client.post(
                "/api/settings/notification-providers/browser-destinations",
                json={
                    **payload,
                    "subscription": {
                        **push_account.subscription,
                        "endpoint": f"https://fcm.googleapis.com/fcm/send/owned-{index}",
                    },
                },
            )
            assert response.status_code == 201, response.text
        response = await client.post(
            "/api/settings/notification-providers/browser-destinations", json=payload
        )
        assert response.status_code == 400 and "20" in response.text


async def test_provider_disable_cancels_work_without_replaying_old_notices(push_account, push_http):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        first = await queue_notice(db, push_account)
        claim = await _claim(db)
        assert claim is not None
        response = await client.put(
            "/api/settings/notification-providers/core.browser", json={"enabled": False}
        )
        assert response.status_code == 200 and response.json()["kind"] == "builtin"
        endpoint = await db.get(NotificationDestination, endpoint_id, populate_existing=True)
        assert endpoint.active and endpoint.encrypted_configuration
        assert not await _dispatch(db, *claim)
        disabled = await queue_notice(db, push_account)
        response = await client.put(
            "/api/settings/notification-providers/core.browser", json={"enabled": True}
        )
        assert response.status_code == 200
        assert await process_pending_deliveries(db) == 0
        assert not push_http.captured
        old = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == first,
                NotificationDelivery.destination_id == endpoint_id,
            )
        )
        assert old.status == "suppressed"
        assert (
            await db.scalar(
                select(NotificationDelivery.id).where(
                    NotificationDelivery.notification_id == disabled,
                    NotificationDelivery.destination_id == endpoint_id,
                )
            )
            is None
        )
        await queue_notice(db, push_account)
        assert await process_pending_deliveries(db) == 1
        assert len(push_http.captured) == 1


async def test_browser_cannot_use_another_providers_opt_in(push_account):
    async with SessionLocal() as db, api(db, push_account) as client:
        endpoint_id = await enroll(client, push_account)
        user = await db.get(User, push_account.user_id)
        endpoint = await db.get(NotificationDestination, endpoint_id)
        foreign_setting = NotificationProviderSetting(
            user_id=user.id, provider_id="core.smtp", enabled=True
        )
        assert (
            await browser.BrowserNotificationProvider().lookup_endpoint(
                db, user, foreign_setting, endpoint
            )
            is None
        )
