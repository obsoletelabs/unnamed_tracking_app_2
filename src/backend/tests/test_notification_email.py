"""Owner enrollment, possession proof and real local SMTP through the core dispatcher."""

import asyncio
import re
import smtplib
import time
from email.parser import BytesParser
from email.policy import default
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from src.api.routes.notification_providers import router
from src.api.routes.notification_unsubscribe import router as unsubscribe_router
from src.core.auth import get_current_user
from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.preferences import load_preferences, save_preferences
from src.core.public_url import normalize_public_url, validate_deployment_url
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_verification import NotificationVerification
from src.database.models.user import User
from src.database.session import SessionLocal, get_db
from src.features.notification_controller import emit_legacy_rows
from src.features.notification_enrollment import (
    EnrollmentError,
    confirm_verification,
    create_email,
    expire_verification_secrets,
    request_verification,
    revoke_email,
    update_email,
)
from src.features.notification_policy import Trust, effective_trust
from src.features.notification_providers.base import NotificationMessage
from src.features.notification_providers.delivery import (
    _lookup_destination,
    process_pending_deliveries,
)
from src.features.notification_providers.smtp import SmtpNotificationProvider, send_mail
from src.features.notification_settings import routing_settings
from src.features.notification_unsubscribe import (
    UNSUBSCRIBE_PATH,
    UnsubscribeError,
    UnsubscribeToken,
    unsubscribe_email,
    unsubscribe_link,
)
from src.features.smtp_configuration import SMTP_PROVIDER, SmtpConfiguration, normalize_email


@pytest.fixture
async def mailbox(monkeypatch):
    received = []

    async def connection(reader, writer):
        writer.write(b"220 local.test ESMTP\r\n")
        await writer.drain()
        try:
            while line := await reader.readline():
                command = line.split(b" ", 1)[0].strip().upper()
                if command == b"DATA":
                    writer.write(b"354 Send message\r\n")
                    await writer.drain()
                    content = bytearray()
                    while (line := await reader.readline()) != b".\r\n":
                        if not line:
                            return
                        content.extend(line[1:] if line.startswith(b"..") else line)
                    received.append(BytesParser(policy=default).parsebytes(bytes(content)))
                writer.write(b"250 local.test\r\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    for name, value in {
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": str(port),
        "SMTP_FROM_ADDRESS": "sender@example.test",
        "SMTP_TLS_MODE": "none",
        "SMTP_USERNAME": "",
        "SMTP_PASSWORD": "",
        "STARTUP_MODE": "default",
    }.items():
        monkeypatch.setenv(name, value)
    yield received
    server.close()
    await server.wait_closed()


@pytest.fixture
async def email_account(mailbox):
    del mailbox
    identity = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=identity,
                username=identity.hex,
                email=f"{identity}@example.test",
                password_hash="unused",
            )
        )
        await db.commit()
    yield identity
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == identity))
        await db.commit()


def draft(kind="plugin"):
    return dict(
        kind=kind,
        media_type="system",
        media_id=uuid4(),
        title="Notice",
        body="User-private content",
        event_at=int(time.time()),
        dedupe_key=str(uuid4()),
    )


async def verify(db, account, endpoint, mailbox):
    challenge = await request_verification(db, account, endpoint)
    assert await process_pending_deliveries(db) == 1
    code = re.search(r"code is ([0-9]{8})", mailbox[-1].get_payload()).group(1)
    assert await confirm_verification(db, account, endpoint, challenge, code)
    return challenge, code


@pytest.mark.parametrize(
    "address", ["a\r\nBcc:evil@test", "A <a@test>", "a@test,b@test", "missing", "@test"]
)
def test_email_headers_reject_multiple_or_invalid_mailboxes(address):
    with pytest.raises(ValueError):
        normalize_email(address)


@pytest.mark.parametrize("mode,port", [("starttls", 587), ("ssl", 465), ("none", 25)])
def test_transport_defaults_preserve_explicit_ports(mode, port):
    assert SmtpConfiguration(tls_mode=mode).port == port
    assert SmtpConfiguration.model_validate({"tls_mode": mode, "port": None}).port == port
    assert SmtpConfiguration(tls_mode=mode, port=2525).port == 2525


@pytest.mark.parametrize(
    "host,mode,allowed",
    [
        ("127.0.0.1", "default", True),
        ("10.1.2.3", "default", True),
        ("192.168.1.1", "default", True),
        ("172.31.1.1", "default", True),
        ("172.32.1.1", "default", False),
        ("::1", "default", True),
        ("fd00::1", "default", True),
        ("8.8.8.8", "default", False),
        ("localhost", "default", False),
        ("smtp.test", "testing", False),
        ("smtp.test", "development", True),
    ],
)
def test_sensitive_plaintext_exception_is_development_or_literal_local_ip(
    monkeypatch, host, mode, allowed
):
    monkeypatch.setenv("STARTUP_MODE", mode)
    config = SmtpConfiguration(host=host, tls_mode="none")
    assert config.allows_sensitive is allowed
    assert SmtpConfiguration(host=host, tls_mode="starttls").allows_sensitive


async def test_multidestinations_encrypt_addresses_and_deliver_only_selected_routes(
    email_account, mailbox
):
    async with SessionLocal() as db:
        first = await create_email(db, email_account, "first@example.test", "Home")
        second = await create_email(db, email_account, "second@example.test", "Work")
        row = await db.get(NotificationDestination, first)
        assert "first@example.test" not in row.encrypted_configuration
        assert effective_trust(row) == Trust.PRIVATE
        await save_preferences(
            db,
            email_account,
            {
                "notification_routes": {
                    "plugin.notice": {
                        str(first): {"enabled": False, "urgency": "normal"},
                        str(second): {"enabled": True, "urgency": "critical"},
                    }
                }
            },
        )
        notices = await emit_legacy_rows(db, email_account, [draft()])
        await db.commit()
        assert await process_pending_deliveries(db) == 1
        assert mailbox[-1]["To"] == "second@example.test"
        assert mailbox[-1]["Importance"] == "high"
        delivery = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notices[0],
                NotificationDelivery.destination_id == second,
            )
        )
        assert delivery.requested_urgency == delivery.effective_urgency == "critical"
        metadata = await routing_settings(db, email_account)
        assert "first@example.test" not in str(metadata)
        assert "encrypted_configuration" not in str(metadata)
        smtp = next(p for p in metadata["providers"] if p["id"] == "core.smtp")
        assert smtp["transport_warning"] and smtp["secure_transport"]


async def test_endpoint_lookup_cannot_substitute_another_address_revision(email_account):
    async with SessionLocal() as db:
        first = await create_email(db, email_account, "first@example.test", "Home")
        second = await create_email(db, email_account, "second@example.test", "Work")
        provider = SmtpNotificationProvider()
        user = await db.get(User, email_account)
        setting = SimpleNamespace(enabled=True, user_id=email_account, provider_id=SMTP_PROVIDER)
        destination = await provider.lookup_endpoint(
            db, user, setting, await db.get(NotificationDestination, second)
        )

        class IncorrectAdapter:
            async def lookup_endpoint(self, *args):
                return destination

        assert (
            await _lookup_destination(
                IncorrectAdapter(), db, user, setting, await db.get(NotificationDestination, first)
            )
            is None
        )


async def test_codes_never_enter_inbox_and_proof_is_single_use(email_account, mailbox):
    async with SessionLocal() as db:
        endpoint = await create_email(db, email_account, "proof@example.test", "Email")
        challenge, code = await verify(db, email_account, endpoint, mailbox)
        proof = await db.get(NotificationVerification, challenge)
        notice = await db.get(Notification, proof.notification_id)
        assert not notice.inbox_visible and code not in notice.body
        assert proof.encrypted_code is None and proof.code_digest != code
        destinations = list(
            await db.scalars(
                select(NotificationDelivery.destination_id).where(
                    NotificationDelivery.notification_id == notice.id
                )
            )
        )
        assert destinations == [endpoint]
        assert effective_trust(await db.get(NotificationDestination, endpoint)) == Trust.SECURE
        assert not await confirm_verification(db, email_account, endpoint, challenge, code)
        await update_email(db, email_account, endpoint, {"recovery_allowed": True})
        await emit_legacy_rows(db, email_account, [draft("session_anomaly")])
        await db.commit()
        assert await process_pending_deliveries(db) == 1
        assert "User-private content" in mailbox[-1].get_payload()


@pytest.mark.parametrize("mutation", ["address", "revoke", "remove"])
async def test_revision_changes_clear_proof_and_cancel_queued_security(
    email_account, mailbox, mutation
):
    async with SessionLocal() as db:
        endpoint = await create_email(db, email_account, "proof@example.test", "Email")
        await verify(db, email_account, endpoint, mailbox)
        await emit_legacy_rows(db, email_account, [draft("session_anomaly")])
        await db.commit()
        if mutation == "address":
            await update_email(db, email_account, endpoint, {"address": "replacement@example.test"})
        else:
            await revoke_email(db, email_account, endpoint, remove=mutation == "remove")
        row = await db.get(NotificationDestination, endpoint)
        assert effective_trust(row) == Trust.PRIVATE and not row.recovery_allowed
        assert await process_pending_deliveries(db) == 0
        assert len(mailbox) == 1
        if mutation == "remove":
            assert row.encrypted_configuration is None and not row.active
            replacement = await create_email(db, email_account, "proof@example.test", "Fresh")
            assert replacement != endpoint


async def test_failed_expired_and_superseded_challenges_cannot_promote_trust(email_account):
    async with SessionLocal() as db:
        endpoint = await create_email(db, email_account, "proof@example.test", "Email")
        challenge = await request_verification(db, email_account, endpoint)
        proof = await db.get(NotificationVerification, challenge)
        code = decrypt_secret(proof.encrypted_code)
        wrong = "00000000" if code != "00000000" else "11111111"
        for _ in range(5):
            assert not await confirm_verification(db, email_account, endpoint, challenge, wrong)
        assert not await confirm_verification(db, email_account, endpoint, challenge, code)
        assert proof.encrypted_code is None
        await db.execute(
            update(NotificationVerification)
            .where(NotificationVerification.id == challenge)
            .values(created_at=int(time.time()) - 61)
        )
        await db.commit()
        new_id = await request_verification(db, email_account, endpoint)
        new = await db.get(NotificationVerification, new_id)
        code = decrypt_secret(new.encrypted_code)
        new.expires_at = int(time.time()) - 1
        await db.commit()
        await expire_verification_secrets(db)
        await db.commit()
        assert not await confirm_verification(db, email_account, endpoint, new_id, code)
        assert effective_trust(await db.get(NotificationDestination, endpoint)) == Trust.PRIVATE


async def test_resend_limits_and_new_revision_invalidate_old_codes(email_account):
    async with SessionLocal() as db:
        endpoint = await create_email(db, email_account, "proof@example.test", "Email")
        challenge = await request_verification(db, email_account, endpoint)
        proof = await db.get(NotificationVerification, challenge)
        code = decrypt_secret(proof.encrypted_code)
        with pytest.raises(EnrollmentError, match="limited"):
            await request_verification(db, email_account, endpoint)
        await db.rollback()
        await db.execute(
            update(NotificationVerification)
            .where(NotificationVerification.id == challenge)
            .values(created_at=int(time.time()) - 61)
        )
        await db.commit()
        await request_verification(db, email_account, endpoint)
        assert not await confirm_verification(db, email_account, endpoint, challenge, code)
        await update_email(db, email_account, endpoint, {"address": "new@example.test"})
        assert not await confirm_verification(db, email_account, endpoint, challenge, code)


async def test_hourly_limits_survive_new_sessions_and_disable_stops_proof(email_account):
    async with SessionLocal() as db:
        endpoint = await create_email(db, email_account, "proof@example.test", "Email")
        for _ in range(10):
            challenge = await request_verification(db, email_account, endpoint)
            await db.execute(
                update(NotificationVerification)
                .where(NotificationVerification.id == challenge)
                .values(created_at=int(time.time()) - 61)
            )
            await db.commit()
    async with SessionLocal() as db:
        with pytest.raises(EnrollmentError, match="limited"):
            await request_verification(db, email_account, endpoint)
        await db.rollback()
        assert effective_trust(await db.get(NotificationDestination, endpoint)) == Trust.PRIVATE
        await update_email(db, email_account, endpoint, {"enabled": False})
        await db.execute(
            delete(NotificationVerification).where(
                NotificationVerification.user_id == email_account
            )
        )
        await db.commit()
        with pytest.raises(EnrollmentError, match="Enable"):
            await request_verification(db, email_account, endpoint)


async def test_foreign_owner_cannot_edit_revoke_or_verify(email_account):
    async with SessionLocal() as db:
        endpoint = await create_email(db, email_account, "proof@example.test", "Email")
        challenge = await request_verification(db, email_account, endpoint)
        stranger = uuid4()
        db.add(
            User(
                id=stranger,
                username=stranger.hex,
                email=f"{stranger}@example.test",
                password_hash="unused",
            )
        )
        await db.commit()
        try:
            for operation in (
                lambda: update_email(db, stranger, endpoint, {"label": "Changed"}),
                lambda: revoke_email(db, stranger, endpoint),
                lambda: confirm_verification(db, stranger, endpoint, challenge, "12345678"),
            ):
                with pytest.raises(EnrollmentError) as failure:
                    await operation()
                assert failure.value.status_code == 404
                await db.rollback()
        finally:
            await db.execute(delete(User).where(User.id == stranger))
            await db.commit()


async def test_public_plaintext_blocks_verification_and_security_but_allows_normal(
    email_account, mailbox, monkeypatch
):
    async with SessionLocal() as db:
        endpoint = await create_email(db, email_account, "proof@example.test", "Email")
        await verify(db, email_account, endpoint, mailbox)
        await emit_legacy_rows(db, email_account, [draft("session_anomaly")])
        await db.commit()
        monkeypatch.setenv("SMTP_HOST", "smtp.external.test")
        with pytest.raises(EnrollmentError, match="secure transport"):
            await request_verification(db, email_account, endpoint)
        await db.rollback()
        send = Mock()
        monkeypatch.setattr("src.features.notification_providers.smtp.send_mail", send)
        assert await process_pending_deliveries(db) == 0
        send.assert_not_called()
        await emit_legacy_rows(db, email_account, [draft()])
        await db.commit()
        assert await process_pending_deliveries(db) == 1
        assert send.call_count == 1
        metadata = await routing_settings(db, email_account)
        email = next(d for d in metadata["destinations"] if d["id"] == str(endpoint))
        assert "security.session.anomaly" not in email["eligible_types"]


@pytest.mark.parametrize("tls", ["starttls", "ssl", "none"])
def test_transport_uses_verified_tls_and_priority_headers(monkeypatch, tls):
    server = Mock()
    factory = Mock(return_value=server)
    monkeypatch.setattr(smtplib, "SMTP", factory)
    monkeypatch.setattr(smtplib, "SMTP_SSL", factory)
    config = SmtpConfiguration(
        host="smtp.test",
        username="user",
        password="secret",
        from_address="from@example.test",
        tls_mode=tls,
    )
    message = NotificationMessage(
        uuid4(), "plugin", "Title\nextra", "Body", "system", uuid4(), 1, urgency="normal"
    )
    send_mail(config, "to@example.test", message)
    mail = server.send_message.call_args.args[0]
    assert mail["Subject"] == "Title extra" and mail["Importance"] is None
    if tls == "starttls":
        context = server.starttls.call_args.kwargs["context"]
        assert context.check_hostname
    elif tls == "ssl":
        assert factory.call_args.kwargs["context"].check_hostname
    else:
        server.starttls.assert_not_called()
    server.login.assert_called_once_with("user", "secret")
    server.close.assert_called_once()


@pytest.mark.parametrize("code,retryable", [(450, True), (550, False)])
async def test_recipient_failures_remain_retryable_only_when_transient(
    email_account, monkeypatch, code, retryable
):
    monkeypatch.setattr(
        "src.features.notification_providers.smtp.send_mail",
        Mock(side_effect=smtplib.SMTPRecipientsRefused({"to@example.test": (code, b"refused")})),
    )
    async with SessionLocal() as db:
        endpoint = await create_email(db, email_account, "to@example.test", "Email")
        provider = SmtpNotificationProvider()
        setting = SimpleNamespace(enabled=True, user_id=email_account, provider_id=SMTP_PROVIDER)
        destination = await provider.lookup_endpoint(
            db,
            await db.get(User, email_account),
            setting,
            await db.get(NotificationDestination, endpoint),
        )
        result = await provider.deliver(
            db,
            destination,
            NotificationMessage(uuid4(), "plugin", "Title", "Body", "system", uuid4(), 1),
        )
        assert not result.success and result.retryable is retryable


async def test_enrollment_api_owner_scope_and_unforgeable_trust(email_account):
    app = FastAPI()
    app.include_router(router)
    async with SessionLocal() as db:
        current = User(id=email_account)
        app.dependency_overrides[get_current_user] = lambda: current
        app.dependency_overrides[get_db] = lambda: db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            base = "/api/settings/notification-providers/email-destinations"
            response = await client.post(
                base, json={"address": "me@example.test", "trust": "SECURE"}
            )
            assert response.status_code == 422
            response = await client.post(base, json={"address": "me@example.test"})
            assert response.status_code == 201
            endpoint = response.json()["id"]
            response = await client.patch(f"{base}/{endpoint}", json={"recovery_allowed": True})
            assert response.status_code == 400
            for method, path, body in [
                ("patch", str(uuid4()), {"enabled": False}),
                ("post", f"{uuid4()}/verification", None),
                ("delete", str(uuid4()), None),
            ]:
                response = await client.request(method, f"{base}/{path}", json=body)
                assert response.status_code == 404
            response = await client.post(f"{base}/{endpoint}/verification")
            assert response.status_code == 200 and "code" not in response.json()
            response = await client.post(f"{base}/{endpoint}/verification")
            assert response.status_code == 429


def link_token(link):
    return parse_qs(urlsplit(link).query)["token"][0]


@pytest.mark.parametrize(
    "value",
    [
        "https://user:password@external.test",
        "https://external.test/app",
        "https://external.test?next=evil",
        "https://external.test#fragment",
        "//external.test",
        "https://external.test\nBcc:bad",
        "https://external.test:invalid",
        "https://external.test<injected>",
    ],
)
def test_public_link_origin_rejects_unsafe_configuration(value):
    with pytest.raises(ValueError):
        normalize_public_url(value)


@pytest.mark.parametrize(
    "value", ["http://external.test", "https://localhost", "https://192.168.1.2", "https://app.lan"]
)
def test_shared_default_requires_public_fqdn(value):
    with pytest.raises(ValueError):
        validate_deployment_url(value)


@pytest.mark.parametrize(
    "value,normalized",
    [
        ("https://app.example.test/", "https://app.example.test"),
        ("http://localhost:5174/", "http://localhost:5174"),
        ("http://127.0.0.1:5174", "http://127.0.0.1:5174"),
        ("http://[::1]:5174/", "http://[::1]:5174"),
    ],
)
def test_public_link_origin_normalizes_explicit_hosts(value, normalized):
    assert normalize_public_url(value) == normalized


@pytest.mark.parametrize("scope", [None, "all"])
async def test_unsubscribe_header_and_get_post_destination_isolation(
    email_account, mailbox, monkeypatch, scope
):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test")
    app = FastAPI()
    app.include_router(unsubscribe_router)
    async with SessionLocal() as db:
        first = await create_email(db, email_account, "first@example.test", "Private label")
        second = await create_email(db, email_account, "second@example.test", "Second")
        await verify(db, email_account, first, mailbox)
        assert "List-Unsubscribe" not in mailbox[-1]
        await update_email(db, email_account, first, {"recovery_allowed": True})
        await emit_legacy_rows(db, email_account, [draft()])
        assert await process_pending_deliveries(db) == 2
        received = next(mail for mail in reversed(mailbox) if mail["To"] == "first@example.test")
        link = received["List-Unsubscribe"].strip("<>")
        assert link.startswith("https://app.example.test" + UNSUBSCRIBE_PATH)
        assert "first@example.test" not in link and "Private label" not in link
        assert "List-Unsubscribe-Post" not in received
        assert link not in received.get_body(preferencelist=("plain",)).get_content()
        html = received.get_body(preferencelist=("html",)).get_content()
        assert f'href="{link}"' in html and ">here</a>" in html
        assert "=?" not in received["List-Unsubscribe"]
        await emit_legacy_rows(db, email_account, [draft()])
        await emit_legacy_rows(db, email_account, [draft("season_started")])
        await db.commit()
        endpoint = await db.get(NotificationDestination, first)
        revision = endpoint.revision
        encrypted = endpoint.encrypted_configuration
        app.dependency_overrides[get_db] = lambda: db
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://forged-host.test"
        ) as client:
            token = link_token(link)
            for _ in range(2):
                response = await client.get(UNSUBSCRIBE_PATH, params={"token": token})
                assert response.status_code == 200
                assert "Unsubscribe from this notification type?" in response.text
                assert "plugin.notice" in response.text
                assert 'name="scope" value="type"' in response.text
                assert 'name="scope" value="all"' in response.text
                assert "/settings?area=account" in response.text
                assert "first@example.test" not in response.text
                assert response.headers["referrer-policy"] == "no-referrer"
                assert response.headers["cache-control"] == "no-store"
                assert endpoint.enabled and endpoint.revision == revision
            response = await client.post(UNSUBSCRIBE_PATH, data={"token": token})
            assert response.status_code == 400 and endpoint.enabled
            response = await client.post(
                UNSUBSCRIBE_PATH,
                data={"token": token, "confirm": "unsubscribe"},
                headers={"sec-fetch-site": "cross-site"},
            )
            assert response.status_code == 400 and endpoint.enabled
            for _ in range(2):
                values = {"token": token, "confirm": "unsubscribe"}
                if scope:
                    values["scope"] = scope
                response = await client.post(UNSUBSCRIBE_PATH, data=values)
                assert response.status_code == 200
            if scope == "all":
                assert not endpoint.enabled and endpoint.revision == revision + 1
            else:
                assert endpoint.enabled and endpoint.revision == revision
                preferences = await load_preferences(db, email_account)
                assert not preferences["notification_routes"]["plugin.notice"][str(first)][
                    "enabled"
                ]
            assert endpoint.encrypted_configuration == encrypted
            assert effective_trust(endpoint) == Trust.SECURE and endpoint.recovery_allowed
            assert (await db.get(NotificationDestination, second)).enabled
            queued = list(
                (
                    await db.scalars(
                        select(NotificationDelivery).where(
                            NotificationDelivery.destination_id == first
                        )
                    )
                ).all()
            )
            if scope == "all":
                assert all(delivery.status in {"sent", "suppressed"} for delivery in queued)
            else:
                assert any(delivery.status == "pending" for delivery in queued)
            reason = "email_unsubscribed" if scope else "email_type_unsubscribed"
            assert any(delivery.last_error == reason for delivery in queued)
            assert await db.scalar(
                select(Notification.id).where(Notification.user_id == email_account)
            )
            # The second email receives both types; type-only opt-out keeps the first's other type.
            assert await process_pending_deliveries(db) == (2 if scope else 3)
            if scope == "all":
                await update_email(db, email_account, first, {"enabled": True})
            else:
                await update_email(
                    db, email_account, first, {"address": "replacement@example.test"}
                )
            response = await client.post(
                UNSUBSCRIBE_PATH, data={"token": token, "confirm": "unsubscribe"}
            )
            await db.refresh(endpoint)
            assert response.status_code == 400 and endpoint.enabled


@pytest.mark.parametrize(
    "mutation", ["forged", "expired", "owner", "purpose", "revision", "address", "removed"]
)
async def test_unsubscribe_invalid_authority_cannot_change_activation(
    email_account, monkeypatch, mutation
):
    async with SessionLocal() as db:
        identity = await create_email(db, email_account, "owner@example.test", "Email")
        endpoint = await db.get(NotificationDestination, identity)
        token = link_token(unsubscribe_link("https://app.example.test", endpoint, "plugin.notice"))
        if mutation == "forged":
            token = "not-an-authenticated-token"
        elif mutation == "expired":
            monkeypatch.setattr(
                "src.features.notification_unsubscribe.time.time", lambda: 9999999999
            )
        elif mutation in {"owner", "purpose", "revision"}:
            import json

            payload = json.loads(decrypt_secret(token))
            payload[mutation] = {"owner": str(uuid4()), "purpose": "verify.email", "revision": 999}[
                mutation
            ]
            token = encrypt_secret(json.dumps(payload))
        elif mutation == "address":
            await update_email(db, email_account, identity, {"address": "changed@example.test"})
        else:
            await revoke_email(db, email_account, identity, remove=True)
        before = (endpoint.enabled, endpoint.revision, endpoint.verified_revision)
        with pytest.raises(UnsubscribeError):
            await unsubscribe_email(db, token)
        await db.refresh(endpoint)
        assert (endpoint.enabled, endpoint.revision, endpoint.verified_revision) == before


@pytest.mark.parametrize("purpose", ["verification", "recovery", "security"])
async def test_transactional_messages_do_not_advertise_unsubscribe(
    email_account, mailbox, monkeypatch, purpose
):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://app.example.test")
    async with SessionLocal() as db:
        identity = await create_email(db, email_account, "owner@example.test", "Email")
        destination = await SmtpNotificationProvider().lookup_endpoint(
            db,
            await db.get(User, email_account),
            SimpleNamespace(user_id=email_account, enabled=True, provider_id=SMTP_PROVIDER),
            await db.get(NotificationDestination, identity),
        )
        message = NotificationMessage(
            uuid4(), "notice", "Title", "Body", "system", uuid4(), 1, purpose=purpose
        )
        assert (await SmtpNotificationProvider().deliver(db, destination, message)).success
        assert "List-Unsubscribe" not in mailbox[-1]
        assert "email-unsubscribe" not in mailbox[-1].get_payload()


def test_subscription_email_html_escapes_content_without_exposing_link_in_plain_text(monkeypatch):
    server = Mock()
    monkeypatch.setattr(smtplib, "SMTP", Mock(return_value=server))
    config = SmtpConfiguration(host="127.0.0.1", from_address="from@example.test", tls_mode="none")
    message = NotificationMessage(
        uuid4(),
        "plugin",
        "<script>Title</script>",
        "<img src=x> & text\nNext",
        "system",
        uuid4(),
        1,
    )
    link = "https://app.example.test/api/notifications/email-unsubscribe?token=opaque"
    send_mail(config, "to@example.test", message, link)
    mail = server.send_message.call_args.args[0]
    assert mail["List-Unsubscribe"] == f"<{link}>"
    assert mail.get_body(preferencelist=("plain",)).get_content().strip() == message.body
    html = mail.get_body(preferencelist=("html",)).get_content()
    assert "<script>" not in html and "<img src=x>" not in html
    assert "&lt;script&gt;Title&lt;/script&gt;" in html and "&amp; text" in html
    assert f'href="{link}">here</a>' in html


async def test_legacy_unsubscribe_token_remains_explicit_all_type_opt_out(email_account):
    async with SessionLocal() as db:
        identity = await create_email(db, email_account, "legacy@example.test", "Email")
        endpoint = await db.get(NotificationDestination, identity)
        token = encrypt_secret(
            UnsubscribeToken(
                purpose="email.unsubscribe.v1",
                owner=email_account,
                destination=identity,
                revision=endpoint.revision,
                address_key=endpoint.endpoint_key,
                expires=int(time.time()) + 3600,
            ).model_dump_json(exclude_none=True)
        )
        app = FastAPI()
        app.include_router(unsubscribe_router)
        app.dependency_overrides[get_db] = lambda: db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            page = await client.get(UNSUBSCRIBE_PATH, params={"token": token})
            assert page.status_code == 200 and "older link" in page.text
            assert 'name="scope" value="type"' not in page.text
            assert 'name="scope" value="all"' in page.text
            invalid = await client.post(
                UNSUBSCRIBE_PATH,
                data={
                    "token": token,
                    "confirm": "unsubscribe",
                    "scope": "type",
                },
            )
            await db.refresh(endpoint)
            assert invalid.status_code == 400 and endpoint.enabled
            response = await client.post(
                UNSUBSCRIBE_PATH,
                data={
                    "token": token,
                    "confirm": "unsubscribe",
                    "scope": "all",
                },
            )
            assert response.status_code == 200 and not endpoint.enabled


async def test_type_opt_out_preserves_security_other_routes_and_requested_urgency(
    email_account, mailbox
):
    async with SessionLocal() as db:
        identity = await create_email(db, email_account, "scoped@example.test", "Email")
        await verify(db, email_account, identity, mailbox)
        await save_preferences(
            db,
            email_account,
            {
                "notification_routes": {
                    "plugin.notice": {str(identity): {"enabled": True, "urgency": "critical"}},
                    "media.season.started": {str(identity): {"enabled": True, "urgency": "normal"}},
                }
            },
        )
        notice_ids = []
        for kind in ("plugin", "season_started", "session_anomaly"):
            notice_ids.extend(await emit_legacy_rows(db, email_account, [draft(kind)]))
        await db.commit()
        endpoint = await db.get(NotificationDestination, identity)
        token = link_token(unsubscribe_link("https://app.example.test", endpoint, "plugin.notice"))
        assert await unsubscribe_email(db, token) == "plugin.notice"
        preferences = await load_preferences(db, email_account)
        assert preferences["notification_routes"]["plugin.notice"][str(identity)] == {
            "enabled": False,
            "urgency": "critical",
        }
        assert preferences["notification_routes"]["media.season.started"][str(identity)]["enabled"]
        assert endpoint.enabled and endpoint.revision == 1
        ordinary = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice_ids[1],
                NotificationDelivery.destination_id == identity,
            )
        )
        assert ordinary.status == "pending"
        security = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice_ids[2],
                NotificationDelivery.destination_id == identity,
            )
        )
        assert security.status == "pending"
        assert effective_trust(endpoint) == Trust.SECURE
        assert len(notice_ids) == 3  # Other types and their inbox entries are retained.


async def test_concurrent_type_opt_outs_preserve_both_destination_choices(email_account):
    async with SessionLocal() as db:
        first = await create_email(db, email_account, "first@example.test", "First")
        second = await create_email(db, email_account, "second@example.test", "Second")
        await save_preferences(db, email_account, {"ui_theme": "dark"})
        tokens = [
            link_token(
                unsubscribe_link(
                    "https://app.example.test",
                    await db.get(NotificationDestination, identity),
                    "plugin.notice",
                )
            )
            for identity in (first, second)
        ]

    async def stop(token):
        async with SessionLocal() as db:
            return await unsubscribe_email(db, token)

    assert await asyncio.gather(*(stop(token) for token in tokens)) == [
        "plugin.notice",
        "plugin.notice",
    ]
    async with SessionLocal() as db:
        preferences = await load_preferences(db, email_account)
        assert preferences["ui_theme"] == "dark"
        choices = preferences["notification_routes"]["plugin.notice"]
        assert not choices[str(first)]["enabled"] and not choices[str(second)]["enabled"]


async def test_type_opt_out_preference_and_queued_suppression_roll_back_together(
    email_account, monkeypatch
):
    async with SessionLocal() as db:
        identity = await create_email(db, email_account, "owner@example.test", "Email")
        notices = await emit_legacy_rows(db, email_account, [draft()])
        await db.commit()
        endpoint = await db.get(NotificationDestination, identity)
        token = link_token(unsubscribe_link("https://app.example.test", endpoint, "plugin.notice"))

        async def fail_suppression(*_args):
            raise RuntimeError("owned transaction failure")

        monkeypatch.setattr(
            "src.features.notification_unsubscribe.retire_destination_type_work", fail_suppression
        )
        with pytest.raises(RuntimeError, match="owned transaction failure"):
            await unsubscribe_email(db, token)
        await db.rollback()
        assert (
            "plugin.notice"
            not in (await load_preferences(db, email_account))["notification_routes"]
        )
        delivery = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notices[0],
                NotificationDelivery.destination_id == identity,
            )
        )
        assert delivery.status == "pending"
