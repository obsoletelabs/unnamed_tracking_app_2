"""Narrow installable auth integrations; plugins never receive account credentials."""

from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationInfo, field_validator

from src.plugin_api.base_contracts import ContractModel


class PasswordResetFlowDefinition(ContractModel):
    mode: Literal["password_reset"]
    name: str = Field(min_length=1, max_length=80)
    token_lifetime_seconds: int = Field(default=1200, ge=600, le=3600)


class UserInviteFlowDefinition(ContractModel):
    mode: Literal["user_invite"]
    name: str = Field(min_length=1, max_length=80)
    token_lifetime_seconds: int = Field(default=86400, ge=3600, le=604800)
    sender_name: str = Field(default="", max_length=80)
    email_subject: str = Field(default="You are invited", min_length=1, max_length=160)
    email_heading: str = Field(default="Join our library", min_length=1, max_length=160)
    welcome_message: str = Field(
        default="You have been invited to create an account.", max_length=2000
    )
    greeting: Literal["personal", "generic", "none"] = "personal"
    button_label: str = Field(default="Accept invitation", min_length=1, max_length=80)
    email_layout: Literal["card", "simple"] = "card"
    accent_color: str = Field(default="#3458d4", pattern=r"^#[0-9a-fA-F]{6}$")
    footer_message: str = Field(default="Sent by your tracking app.", max_length=500)

    @field_validator("sender_name", "email_subject", "email_heading", "button_label")
    @classmethod
    def single_line_text(cls, value: str, info: ValidationInfo) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Email labels must not contain control characters")
        if info.field_name != "sender_name" and not value.strip():
            raise ValueError("Email labels must not be empty")
        return value.strip()


class UserInviteIssue(ContractModel):
    email: str = Field(min_length=3, max_length=254)
    recipient_name: str = Field(default="", max_length=80)

    @field_validator("recipient_name")
    @classmethod
    def validate_recipient_name(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Recipient name must not contain control characters")
        return value.strip()


class UserInviteIdentity(ContractModel):
    id: UUID
