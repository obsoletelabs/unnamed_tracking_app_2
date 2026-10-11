"""Narrow installable auth integrations; plugins never receive account credentials."""

from typing import Literal

from pydantic import Field

from src.plugin_api.base_contracts import ContractModel


class PasswordResetFlowDefinition(ContractModel):
    mode: Literal["password_reset"]
    name: str = Field(min_length=1, max_length=80)
    token_lifetime_seconds: int = Field(default=1200, ge=600, le=3600)
