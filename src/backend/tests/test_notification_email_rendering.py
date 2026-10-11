"""All email purposes share rich MIME; one-click opt-out remains narrowly scoped."""

import smtplib
from email.utils import parsedate_to_datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes.notification_unsubscribe import router
from src.core.preferences import load_preferences
from src.database.models.notification_destination import NotificationDestination
from src.database.session import SessionLocal, get_db
from src.features.notification_enrollment import create_email
from src.features.notification_providers.base import NotificationMessage
from src.features.notification_providers.smtp import send_mail
from src.features.notification_unsubscribe import UNSUBSCRIBE_PATH, unsubscribe_link
from src.features.smtp_configuration import SmtpConfiguration
from tests.test_notification_email import email_account as email_account
from tests.test_notification_email import mailbox as mailbox


@pytest.mark.parametrize(
    ("kind", "purpose", "event_type", "label"),
    [
        ("provider_test", "test", "", "Delivery test"),
        ("destination_verification", "standard", "", "Verify email address"),
        ("security", "recovery", "auth.password_reset", "Reset password"),
        ("invitation", "invitation", "auth.user_invite", "Accept invitation"),
        ("plugin", "standard", "plugin.update", "Open notification"),
    ],
)
def test_every_purpose_has_readable_html_and_plain_alternative(
    monkeypatch, kind, purpose, event_type, label
):
    server = Mock()
    monkeypatch.setattr(smtplib, "SMTP", Mock(return_value=server))
    link = "https://app.example.test/action#token=opaque"
    message = NotificationMessage(
        uuid4(),
        kind,
        "A <safe> title",
        "Readable instructions.\n" + link,
        "system",
        uuid4(),
        1,
        purpose=purpose,
        event_type=event_type,
        action_url=link,
    )
    send_mail(
        SmtpConfiguration(host="127.0.0.1", from_address="app@example.test", tls_mode="none"),
        "to@example.test",
        message,
    )
    mail = server.send_message.call_args.args[0]
    assert mail.get_content_type() == "multipart/alternative"
    assert parsedate_to_datetime(mail["Date"]).tzinfo is not None
    assert mail["Message-ID"] == f"<{message.id}@example.test>"
    assert mail.get_body(preferencelist=("plain",)).get_content().strip() == message.body
    html = mail.get_body(preferencelist=("html",)).get_content()
    assert "A &lt;safe&gt; title" in html and "<safe>" not in html
    assert f'href="{link}"' in html and label in html
    assert html.count(link) == 1  # No long bearer URL in the visible paragraph.
    assert "List-Unsubscribe" not in mail and "Precedence" not in mail


@pytest.mark.parametrize("scheme", ["https", "http"])
def test_subscription_headers_identify_list_and_https_one_click(monkeypatch, scheme):
    server = Mock()
    monkeypatch.setattr(smtplib, "SMTP", Mock(return_value=server))
    message = NotificationMessage(
        uuid4(),
        "plugin",
        "Update",
        "A new version is available.",
        "system",
        uuid4(),
        1,
        event_type="plugin.update.available",
    )
    link = f"{scheme}://app.example.test{UNSUBSCRIBE_PATH}?token=opaque"
    send_mail(
        SmtpConfiguration(host="127.0.0.1", from_address="app@example.test", tls_mode="none"),
        "to@example.test",
        message,
        link,
    )
    mail = server.send_message.call_args.args[0]
    assert mail["List-Unsubscribe"] == f"<{link}>"
    assert mail["Precedence"] == "bulk"
    assert "<plugin.update.available.example.test>" in mail["List-ID"]
    assert mail["List-Unsubscribe-Post"] == (
        "List-Unsubscribe=One-Click" if scheme == "https" else None
    )


def test_html_rejects_executable_action_urls(monkeypatch):
    server = Mock()
    monkeypatch.setattr(smtplib, "SMTP", Mock(return_value=server))
    message = NotificationMessage(
        uuid4(),
        "plugin",
        "<script>title</script>",
        "<img src=x onerror=bad>",
        "system",
        uuid4(),
        1,
        action_url="javascript:alert(1)",
    )
    send_mail(
        SmtpConfiguration(host="127.0.0.1", from_address="app@example.test", tls_mode="none"),
        "to@example.test",
        message,
    )
    html = server.send_message.call_args.args[0].get_body(preferencelist=("html",)).get_content()
    assert "<script>" not in html and "<img" not in html and "javascript:" not in html


@pytest.mark.parametrize("multipart", [False, True])
async def test_mailbox_provider_one_click_needs_no_session_and_preserves_other_routes(
    email_account, multipart
):
    async with SessionLocal() as db:
        destination_id = await create_email(db, email_account, "oneclick@example.test", "Email")
        other_id = await create_email(db, email_account, "other@example.test", "Other email")
        destination = await db.get(NotificationDestination, destination_id)
        link = unsubscribe_link("https://app.example.test", destination, "plugin.notice")
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://app.example.test"
        ) as client:
            assert (await client.get(link)).status_code == 200
            assert (await load_preferences(db, email_account))["notification_routes"] == {}
            assert (await client.post(link, data={"List-Unsubscribe": "wrong"})).status_code == 400
            payload = (
                {"files": {"List-Unsubscribe": (None, "One-Click")}}
                if multipart
                else {"data": {"List-Unsubscribe": "One-Click"}}
            )
            for _ in range(2):
                response = await client.post(link, **payload)
                assert response.status_code == 204 and "location" not in response.headers
                assert response.headers["cache-control"] == "no-store"
            preferences = await load_preferences(db, email_account)
            assert (
                preferences["notification_routes"]["plugin.notice"][str(destination_id)]["enabled"]
                is False
            )
            assert str(other_id) not in preferences["notification_routes"]["plugin.notice"]
            assert destination.enabled and (await db.get(NotificationDestination, other_id)).enabled
            assert (
                await client.post(UNSUBSCRIBE_PATH + "?token=forged", **payload)
            ).status_code == 400
