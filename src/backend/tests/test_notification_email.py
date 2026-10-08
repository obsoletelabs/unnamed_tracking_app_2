"""Owner enrollment, possession proof and real local SMTP through the core dispatcher."""

import asyncio
import re
import smtplib
import time
from email.parser import BytesParser
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from src.api.routes.notification_providers import router
from src.core.auth import get_current_user
from src.core.crypto import decrypt_secret
from src.core.preferences import save_preferences
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
from src.features.smtp_configuration import SmtpConfiguration, normalize_email


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
                    received.append(BytesParser().parsebytes(bytes(content)))
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
        setting = SimpleNamespace(enabled=True, user_id=email_account)
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
        setting = SimpleNamespace(enabled=True, user_id=email_account)
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
