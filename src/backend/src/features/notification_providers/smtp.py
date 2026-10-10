"""Built-in SMTP adapter; host-owned credentials and concrete recipient revisions."""

import asyncio
import json
import smtplib
import ssl
import time
from dataclasses import dataclass, replace
from email.message import EmailMessage
from email.policy import SMTP
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.crypto import decrypt_secret
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.notification_verification import NotificationVerification
from src.database.models.user import User
from src.features.notification_unsubscribe import unsubscribe_link
from src.features.notification_urls import notification_url
from src.features.notification_verification_links import verification_link
from src.features.smtp_configuration import (
    SMTP_PROVIDER,
    SmtpConfiguration,
    normalize_email,
    smtp_configuration,
)

from .base import DeliveryResult, NotificationMessage, ProviderDestination
from .eligibility import endpoint_route_enabled


@dataclass(frozen=True)
class SmtpDestination(ProviderDestination):
    address: str = ""
    configuration: SmtpConfiguration | None = None


def send_mail(
    config: SmtpConfiguration,
    address: str,
    message: NotificationMessage,
    unsubscribe_url: str | None = None,
) -> None:
    """Blocking stdlib transport runs in a thread with bounded socket/deadline timeouts."""
    # Keep opaque List-Unsubscribe URLs intact instead of RFC2047-folding them.
    mail = EmailMessage(policy=SMTP.clone(max_line_length=998))
    mail["Subject"] = " ".join(message.title.splitlines())
    mail["From"] = normalize_email(config.from_address)
    mail["To"] = normalize_email(address)
    mail["Message-ID"] = f"<{message.id}@notifications.invalid>"
    if message.urgency == "critical":
        mail["Importance"] = "high"
        mail["X-Priority"] = "1"
    mail.set_content(message.body)
    if unsubscribe_url:
        mail["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        # Plain-text clients retain the body; the header supplies opt-out authority.
        # HTML clients get a short linked footer instead of an exposed bearer URL.
        body = escape(message.body).replace("\n", "<br>\n")
        mail.add_alternative(
            '<!doctype html><html lang="en"><body>'
            f"<h1>{escape(message.title)}</h1><p>{body}</p>"
            f"<p>Manage or unsubscribe from these notifications "
            f'<a href="{escape(unsubscribe_url, quote=True)}">here</a>.</p>'
            "</body></html>",
            subtype="html",
        )
    context = ssl.create_default_context()
    deadline = time.monotonic() + 25
    client = (
        smtplib.SMTP_SSL(config.host, config.port, timeout=8, context=context)
        if config.tls_mode == "ssl"
        else smtplib.SMTP(config.host, config.port, timeout=8)
    )
    try:
        client.ehlo()
        if config.tls_mode == "starttls":
            client.starttls(context=context)
            client.ehlo()
        if config.username:
            client.login(config.username, config.password)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("SMTP deadline exceeded")
        if client.sock:
            client.sock.settimeout(min(8, remaining))
        client.send_message(mail)
    finally:
        # QUIT failure after DATA acceptance must not turn a successful send into a retry.
        client.close()


async def deliver_mail(
    config: SmtpConfiguration,
    address: str,
    message: NotificationMessage,
    unsubscribe_url: str | None = None,
) -> DeliveryResult:
    """Use the same transport and sanitized outcomes for dispatch and admin diagnostics."""
    try:
        await asyncio.to_thread(send_mail, config, address, message, unsubscribe_url)
    except smtplib.SMTPResponseException as exc:
        return DeliveryResult(False, retryable=400 <= exc.smtp_code < 500, error="smtp_rejected")
    except smtplib.SMTPRecipientsRefused as exc:
        retryable = any(400 <= code < 500 for code, _ in exc.recipients.values())
        return DeliveryResult(False, retryable=retryable, error="smtp_recipient_rejected")
    except (OSError, smtplib.SMTPException):
        return DeliveryResult(False, retryable=True, error="smtp_unavailable")
    return DeliveryResult(True)


class SmtpNotificationProvider:
    id = SMTP_PROVIDER
    name = "Email (SMTP)"
    critical_supported = True

    async def lookup_destination(self, db, user, setting):
        del db, user, setting
        # A user's arbitrary first address must never stand in for a selected endpoint.
        return None

    async def lookup_endpoint(
        self,
        db: AsyncSession,
        user: User,
        setting: NotificationProviderSetting | None,
        endpoint: NotificationDestination,
    ) -> SmtpDestination | None:
        if not endpoint_route_enabled(self.id, user.id, setting, endpoint):
            return None
        if not endpoint.active or not endpoint.enabled or not endpoint.encrypted_configuration:
            return None
        config = await smtp_configuration(db)
        if not config.configured:
            return None
        address = json.loads(decrypt_secret(endpoint.encrypted_configuration))["address"]
        return SmtpDestination(
            user_id=user.id,
            display=endpoint.display_name or "Email",
            allows_sensitive=config.allows_sensitive,
            endpoint_id=endpoint.id,
            endpoint_revision=endpoint.revision,
            address=address,
            configuration=config,
        )

    # Early failures distinguish unsafe transport, expired proof and SMTP outcomes.
    # pylint: disable-next=too-many-return-statements
    async def deliver(
        self, db: AsyncSession, destination: ProviderDestination, message: NotificationMessage
    ) -> DeliveryResult:
        if not isinstance(destination, SmtpDestination) or destination.configuration is None:
            return DeliveryResult(False, error="destination_unavailable")
        if message.kind == "destination_verification":
            if not destination.allows_sensitive:
                return DeliveryResult(False, error="transport_security_required")
            challenge = await db.scalar(
                select(NotificationVerification).where(
                    NotificationVerification.notification_id == message.id,
                    NotificationVerification.destination_id == destination.endpoint_id,
                    NotificationVerification.destination_revision == destination.endpoint_revision,
                    NotificationVerification.user_id == destination.user_id,
                    NotificationVerification.expires_at > int(time.time()),
                    NotificationVerification.used_at.is_(None),
                )
            )
            if challenge is None or not challenge.encrypted_code:
                return DeliveryResult(False, error="verification_expired")
            code = decrypt_secret(challenge.encrypted_code)
            endpoint = await db.get(NotificationDestination, destination.endpoint_id)
            origin = await notification_url(db, destination.user_id, endpoint)
            link = verification_link(origin, challenge) if origin else None
            message = replace(
                message,
                body=(
                    f"Your verification code is {code}.\nIt expires in 10 minutes.\n"
                    + (f"Or open this link and confirm to verify:\n{link}\n" if link else "")
                    + "If you did not request this code, ignore this email."
                ),
            )
        link = None
        if message.purpose == "standard" and message.kind != "destination_verification":
            endpoint = await db.get(NotificationDestination, destination.endpoint_id)
            origin = await notification_url(db, destination.user_id, endpoint)
            if origin and endpoint and message.event_type:
                link = unsubscribe_link(origin, endpoint, message.event_type)
        return await deliver_mail(destination.configuration, destination.address, message, link)
