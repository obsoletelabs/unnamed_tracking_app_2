"""SMTP resolves through the existing deployment configuration and encryption boundary."""

import ipaddress
from email.errors import HeaderParseError
from email.headerregistry import Address
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.app_integrations import get_or_create_app_integration_settings
from src.core.crypto import decrypt_secret
from src.core.env_handler import EnvConfigHandler

SMTP_PROVIDER = "core.smtp"
SMTP_FIELDS = {
    "SMTP_HOST": "smtp_host",
    "SMTP_PORT": "smtp_port",
    "SMTP_USERNAME": "smtp_username",
    "SMTP_PASSWORD": "smtp_password",
    "SMTP_FROM_ADDRESS": "smtp_from_address",
    "SMTP_TLS_MODE": "smtp_tls_mode",
}


def normalize_email(value: str) -> str:
    """One mailbox, no display name, control characters or oversized header input."""
    value = value.strip()
    if not value or len(value) > 254 or any(ord(char) < 32 for char in value):
        raise ValueError("Enter a valid email address")
    try:
        address = Address(addr_spec=value)
    except (ValueError, IndexError, HeaderParseError) as exc:
        raise ValueError("Enter a valid email address") from exc
    if not address.username or not address.domain or address.addr_spec != value:
        raise ValueError("Enter a single email address without a display name")
    return Address(username=address.username, domain=address.domain.lower()).addr_spec


class SmtpConfiguration(BaseModel):
    host: str = Field(default="", max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    username: str = Field(default="", max_length=254)
    password: str = Field(default="", max_length=4096, repr=False)
    from_address: str = Field(default="", max_length=254)
    tls_mode: Literal["starttls", "ssl", "none"] = "starttls"

    @field_validator("host", "username")
    @classmethod
    def safe_connection_field(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("SMTP connection fields cannot contain control characters")
        return value.strip()

    @field_validator("from_address")
    @classmethod
    def safe_sender(cls, value: str) -> str:
        return normalize_email(value) if value.strip() else ""

    @property
    def configured(self) -> bool:
        try:
            normalize_email(self.from_address)
        except ValueError:
            return False
        return bool(self.host and not any(char.isspace() for char in self.host))

    @property
    def allows_sensitive(self) -> bool:
        if self.tls_mode != "none" or EnvConfigHandler().mode.value == "development":
            return True
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            return False
        local_networks = (
            "127.0.0.0/8",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "::1/128",
            "fc00::/7",
        )
        return any(address in ipaddress.ip_network(network) for network in local_networks)


def validate_smtp_value(attribute: str, value):
    name = attribute.removeprefix("smtp_")
    if value is None:
        return None
    config = SmtpConfiguration.model_validate({name: value})
    if name == "host" and config.host and any(char.isspace() for char in config.host):
        raise ValueError("SMTP host cannot contain spaces")
    return getattr(config, name)


async def smtp_configuration(db: AsyncSession) -> SmtpConfiguration:
    row = await get_or_create_app_integration_settings(db)
    await db.refresh(row)
    handler = EnvConfigHandler()
    values = {}
    for name, attribute in SMTP_FIELDS.items():
        value = handler.get(name) if handler.has(name) else getattr(row, attribute)
        if name == "SMTP_PASSWORD" and value and not handler.has(name):
            value = decrypt_secret(value)
        if value is not None:
            values[attribute.removeprefix("smtp_")] = value
    return SmtpConfiguration.model_validate(values)
