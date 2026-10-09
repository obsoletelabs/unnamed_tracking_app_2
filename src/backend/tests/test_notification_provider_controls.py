"""Saved SMTP diagnostics, owner-only delivery tests and scanner-safe possession links."""

import re
import smtplib
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from src.api.routes.notification_providers import router as providers_router
from src.api.routes.notification_verification import router as verification_router
from src.core.auth import get_current_user
from src.core.preferences import save_preferences
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.notification_verification import NotificationVerification
from src.database.models.user import User
from src.database.session import SessionLocal, get_db
from src.features.notification_enrollment import (
    create_email,
    request_verification,
    update_email,
)
from src.features.notification_policy import Trust, effective_trust
from src.features.notification_providers.delivery import process_pending_deliveries
from src.features.notification_settings import routing_settings
from src.features.notification_tests import TEST_EVENT
from src.features.notification_verification_links import verification_link
from tests.test_notification_email import mailbox as mailbox


@pytest.fixture
async def owner(mailbox):
    del mailbox
    user_id = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                username=user_id.hex,
                email="account@example.test",
                password_hash="unused",
            )
        )
        await db.commit()
    yield user_id
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def api(db, user):
    app = FastAPI()
    app.include_router(providers_router)
    app.include_router(verification_router)
    identity = user.id

    async def authenticated_user():
        return await db.get(User, identity)

    app.dependency_overrides[get_current_user] = authenticated_user
    app.dependency_overrides[get_db] = lambda: db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_admin_smtp_uses_saved_configuration_and_never_enrolls_address(owner, mailbox):
    async with SessionLocal() as db:
        user = await db.get(User, owner)
        async with api(db, user) as client:
            path = "/api/settings/notification-providers/smtp/test"
            assert (
                await client.post(path, json={"address": "recipient@example.test"})
            ).status_code == 403
            assert mailbox == []
            user.is_admin = True
            await db.commit()
            for address in ["bad", "a@example.test\r\nBcc: other@example.test", "a@test,b@test"]:
                assert (await client.post(path, json={"address": address})).status_code == 422
            response = await client.post(path, json={"address": "recipient@example.test"})
            assert response.json() == {"sent": True, "error": None, "retryable": False}
            assert mailbox[-1]["To"] == "recipient@example.test"
            assert mailbox[-1]["Subject"] == "Test notification"
            assert "delivery test" in mailbox[-1].get_payload()
            assert mailbox[-1]["List-Unsubscribe"] is None
            assert "account@example.test" not in mailbox[-1].get_payload()
            assert (
                await db.scalar(
                    select(NotificationDestination.id).where(
                        NotificationDestination.user_id == owner
                    )
                )
                is None
            )
            assert (
                await db.scalar(select(Notification.id).where(Notification.user_id == owner))
                is None
            )
            for _ in range(9):
                assert (
                    await client.post(path, json={"address": "recipient@example.test"})
                ).status_code == 200
            assert (
                await client.post(path, json={"address": "recipient@example.test"})
            ).status_code == 429
            assert len(mailbox) == 10


async def test_smtp_failure_is_sanitized_and_consumes_quota(owner, monkeypatch):
    sender = Mock(side_effect=smtplib.SMTPAuthenticationError(535, b"secret credential value"))
    monkeypatch.setattr("src.features.notification_providers.smtp.send_mail", sender)
    async with SessionLocal() as db:
        user = await db.get(User, owner)
        user.is_admin = True
        await db.commit()
        async with api(db, user) as client:
            response = await client.post(
                "/api/settings/notification-providers/smtp/test",
                json={"address": "recipient@example.test"},
            )
            assert response.json() == {"sent": False, "error": "smtp_rejected", "retryable": False}
            assert "secret" not in response.text
            assert await db.scalar(
                select(NotificationReceipt.id).where(
                    NotificationReceipt.user_id == owner,
                    NotificationReceipt.event_type == TEST_EVENT,
                )
            )


async def test_destination_tests_are_targeted_and_respect_owner_and_preferences(owner, mailbox):
    async with SessionLocal() as db:
        first = await create_email(db, owner, "first@example.test", "First")
        second = await create_email(db, owner, "second@example.test", "Second")
        async with api(db, await db.get(User, owner)) as client:
            base = "/api/settings/notification-providers/destinations"
            assert (await client.post(f"{base}/{uuid4()}/test")).status_code == 404
            response = await client.post(f"{base}/{first}/test")
            assert response.status_code == 200 and response.json()["status"] == "pending"
            delivery = await db.get(NotificationDelivery, response.json()["delivery_id"])
            work = list(
                await db.scalars(
                    select(NotificationDelivery).where(
                        NotificationDelivery.notification_id == delivery.notification_id
                    )
                )
            )
            assert len(work) == 1 and work[0].destination_id == first
            assert await process_pending_deliveries(db) == 1
            assert [mail["To"] for mail in mailbox] == ["first@example.test"]
            assert not (await db.get(Notification, delivery.notification_id)).inbox_visible
            await save_preferences(db, owner, {"notification_destinations": {str(second): False}})
            await db.commit()
            assert (await client.post(f"{base}/{second}/test")).status_code == 400


async def test_verification_link_uses_same_single_use_challenge_without_get_side_effects(
    owner, mailbox
):
    async with SessionLocal() as db:
        endpoint = await create_email(db, owner, "first@example.test", "First")
        await save_preferences(db, owner, {"notification_url": "https://app.example.test"})
        await db.commit()
        challenge_id = await request_verification(db, owner, endpoint)
        assert await process_pending_deliveries(db) == 1
        body = mailbox[-1].get_payload()
        link = re.search(
            r"https://app.example.test/api/notifications/email-verify\?token=\S+", body
        ).group()
        token = parse_qs(urlsplit(link).query)["token"][0]
        assert "first@example.test" not in link and "owner" not in link
        destination = await db.get(NotificationDestination, endpoint)
        challenge = await db.get(NotificationVerification, challenge_id)
        async with api(db, await db.get(User, owner)) as client:
            path = "/api/notifications/email-verify"
            response = await client.get(path, params={"token": token})
            assert response.status_code == 200 and "Verify this email?" in response.text
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "first@example.test" not in response.text
            await db.refresh(destination)
            await db.refresh(challenge)
            assert effective_trust(destination) == Trust.PRIVATE and challenge.used_at is None
            assert (await client.post(path, data={"token": token})).status_code == 400
            assert (
                await client.post(
                    path,
                    headers={"Sec-Fetch-Site": "cross-site"},
                    data={"token": token, "confirm": "verify"},
                )
            ).status_code == 400
            assert (
                await client.post(path, data={"token": token, "confirm": "verify"})
            ).status_code == 200
            await db.refresh(destination)
            assert effective_trust(destination) == Trust.SECURE and not destination.recovery_allowed
            assert (await client.get(path, params={"token": token})).status_code == 400
            assert (
                await client.post(path, data={"token": token, "confirm": "verify"})
            ).status_code == 400


async def test_replaced_address_and_expired_challenge_invalidate_links(owner):
    async with SessionLocal() as db:
        endpoint = await create_email(db, owner, "first@example.test", "First")
        challenge_id = await request_verification(db, owner, endpoint)
        challenge = await db.get(NotificationVerification, challenge_id)
        link = verification_link("https://app.example.test", challenge)
        token = parse_qs(urlsplit(link).query)["token"][0]
        await update_email(db, owner, endpoint, {"address": "replacement@example.test"})
        async with api(db, await db.get(User, owner)) as client:
            path = "/api/notifications/email-verify"
            assert (
                await client.post(path, data={"token": token, "confirm": "verify"})
            ).status_code == 400
            assert (await client.get(path, params={"token": "forged"})).status_code == 400
            # An expired real challenge still fails even when its link ciphertext is authentic.
            challenge.expires_at = 1
            challenge.encrypted_code = None
            await db.commit()
            assert (await client.get(path, params={"token": token})).status_code == 400
            assert effective_trust(await db.get(NotificationDestination, endpoint)) == Trust.PRIVATE


async def test_price_hooks_do_not_appear_as_live_sources_before_an_observation(owner):
    async with SessionLocal() as db:
        metadata = await routing_settings(db, owner)
        assert "game.released" in {row["event_type"] for row in metadata["types"]}
        assert not {"game.sale.started", "game.price.threshold_hit"} & {
            row["event_type"] for row in metadata["types"]
        }
        db.add(
            NotificationReceipt(
                user_id=owner,
                dedupe_key=str(uuid4()),
                event_type="game.sale.started",
                source="live-store",
                occurred_at=1,
            )
        )
        await db.commit()
        metadata = await routing_settings(db, owner)
        assert "game.sale.started" in {row["event_type"] for row in metadata["types"]}


def test_access_logs_redact_bearer_links_but_preserve_other_paths():
    import logging

    from src.features.notification_logging import NotificationLinkFilter

    for path in ["/api/notifications/email-verify", "/api/notifications/email-unsubscribe"]:
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            "",
            1,
            "%s %s %s %s %s",
            ("client", "GET", f"{path}?token=secret", "1.1", 200),
            None,
        )
        assert NotificationLinkFilter().filter(record)
        assert "secret" not in record.getMessage() and path in record.getMessage()
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        "%s %s %s %s %s",
        ("client", "GET", "/games?search=title", "1.1", 200),
        None,
    )
    assert NotificationLinkFilter().filter(record)
    assert "search=title" in record.getMessage()
