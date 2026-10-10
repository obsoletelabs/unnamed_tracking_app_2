"""Host-owned VAPID configuration; no filesystem keys or plugin-visible secrets."""

import base64
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.app_integrations import get_or_create_app_integration_settings
from src.core.crypto import decrypt_secret, encrypt_secret
from src.core.env_handler import EnvConfigHandler
from src.core.public_url import normalize_public_url
from src.database.models.app_integration_settings import AppIntegrationSettings
from src.features.notification_destinations import retire_provider_destinations
from src.features.notification_policy import PUSH_PROVIDER
from src.features.smtp_configuration import normalize_email

PUSH_FIELDS = {
    "WEB_PUSH_VAPID_SUBJECT": "web_push_vapid_subject",
    "WEB_PUSH_VAPID_PRIVATE_KEY": "web_push_vapid_private_key",
}


class PushKeyEnvironmentLocked(ValueError):
    """An explicit admin key request cannot override an environment-owned key."""


def generate_vapid_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    ).decode("ascii")


def vapid_key(value: str) -> ec.EllipticCurvePrivateKey:
    """A value is key material, never a path accepted by a transport library."""
    try:
        key = serialization.load_der_private_key(base64.b64decode(value, validate=True), None)
    except (ValueError, TypeError) as exc:
        raise ValueError("Enter a base64 DER P-256 VAPID private key") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("VAPID requires a P-256 private key")
    return key


class PushConfiguration(BaseModel):
    subject: str = Field(default="", max_length=1024)
    private_key: str = Field(default="", max_length=2048, repr=False)

    @field_validator("subject")
    @classmethod
    def contact_uri(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if value.startswith("mailto:"):
            return "mailto:" + normalize_email(value.removeprefix("mailto:"))
        normalized = normalize_public_url(value)
        if urlsplit(normalized).scheme != "https":
            raise ValueError("VAPID contact must be a mailto address or HTTPS URL")
        return normalized

    @field_validator("private_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        value = value.strip()
        if value:
            vapid_key(value)
        return value

    @property
    def configured(self) -> bool:
        return bool(self.subject and self.private_key)

    @property
    def public_key(self) -> str:
        if not self.private_key:
            return ""
        public = (
            vapid_key(self.private_key)
            .public_key()
            .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        )
        return base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")


def validate_push_value(attribute: str, value):
    if value is None:
        return None
    name = {"web_push_vapid_subject": "subject", "web_push_vapid_private_key": "private_key"}[
        attribute
    ]
    return getattr(PushConfiguration.model_validate({name: value}), name)


async def push_configuration(db: AsyncSession) -> PushConfiguration:
    row = await get_or_create_app_integration_settings(db)
    await db.refresh(row)
    handler = EnvConfigHandler()
    values = {}
    for name, attribute in PUSH_FIELDS.items():
        value = handler.get(name) if handler.has(name) else getattr(row, attribute)
        if attribute.endswith("private_key") and value and not handler.has(name):
            value = decrypt_secret(value)
        values["private_key" if attribute.endswith("private_key") else "subject"] = value or ""
    return PushConfiguration.model_validate(values)


async def ensure_vapid_key(db: AsyncSession) -> dict[str, str]:
    """Generate only on an explicit admin request; never rotate an existing key."""
    if EnvConfigHandler().has("WEB_PUSH_VAPID_PRIVATE_KEY"):
        raise PushKeyEnvironmentLocked("The VAPID key is managed by the deployment environment")
    singleton = await get_or_create_app_integration_settings(db)
    row = await db.scalar(
        select(AppIntegrationSettings)
        .where(AppIntegrationSettings.id == singleton.id)
        .with_for_update()
    )
    assert row is not None
    if not row.web_push_vapid_private_key:
        await retire_provider_destinations(db, PUSH_PROVIDER)
        row.web_push_vapid_private_key = encrypt_secret(generate_vapid_key())
    await db.commit()
    configuration = await push_configuration(db)
    return {"public_key": configuration.public_key}
