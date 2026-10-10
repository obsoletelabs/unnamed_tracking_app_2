"""Bounded public notification declarations and event facts for Plugin API v1."""

from string import Formatter
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from .base_contracts import ContractModel


class NotificationProviderDeclaration(ContractModel):
    """A plugin declares its destination contract; the plugin performs delivery."""

    provider_id: str = Field(
        min_length=3,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    name: str = Field(min_length=1, max_length=128)
    action_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    destination_kind: str = Field(
        default="plugin",
        min_length=1,
        max_length=32,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    channel_context: Literal["external", "internal"] = "external"
    privacy: Literal["PUBLIC", "PRIVATE"] = "PUBLIC"
    transport: Literal["plugin_public", "plugin_private"] = "plugin_public"

    @model_validator(mode="after")
    def synchronize_privacy(self) -> Self:
        expected = "plugin_private" if self.privacy == "PRIVATE" else "plugin_public"
        if self.transport != expected:
            raise ValueError("transport must match the declared provider privacy")
        return self


class NotificationTypeRegistration(ContractModel):
    """Host-interpreted plain-text templates; producers do not choose delivery endpoints."""

    event_type: str = Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=500)
    required_trust: Literal["PRIVATE", "SECURE"] = "PRIVATE"
    purpose: Literal["standard", "security", "recovery"] = "standard"
    severity: Literal["info", "warning", "error"] = "info"
    title_template: str = Field(min_length=1, max_length=500)
    body_template: str = Field(min_length=1, max_length=10000)
    parameters: dict[str, Literal["string", "integer", "boolean"]] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def validate_policy_and_templates(self) -> Self:
        if self.purpose != "standard" and self.required_trust != "SECURE":
            raise ValueError("Security and recovery sources require SECURE destinations")
        if any(not key.isidentifier() or len(key) > 32 for key in self.parameters):
            raise ValueError("Parameters require short named identifiers")
        for template in (self.title_template, self.body_template):
            for _, name, spec, conversion in Formatter().parse(template):
                if name is not None and (name not in self.parameters or spec or conversion):
                    raise ValueError("Templates support only declared scalar parameter names")
        return self


class NotificationEventEmission(ContractModel):
    """Facts for one installed source; the gateway supplies the recipient and installation."""

    event_type: str = Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    dedupe_key: str = Field(min_length=1, max_length=64)
    occurred_at: StrictInt = Field(ge=0)
    data: dict[str, StrictStr | StrictInt | StrictBool] = Field(default_factory=dict, max_length=32)
    group_key: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def bound_values(self) -> Self:
        values = dict(self.data)
        if any(isinstance(value, str) and len(value) > 1000 for value in values.values()):
            raise ValueError("Event string parameters exceed their bounds")
        if any(isinstance(value, int) and abs(value) > 2**53 - 1 for value in values.values()):
            raise ValueError("Event integer parameters exceed their bounds")
        return self


class NotificationFieldLayout(ContractModel):
    """A provider can arrange approved fields, never append arbitrary destination content."""

    style: Literal["plain", "embed"] = "embed"
    fields: tuple[Literal["title", "body", "event_at", "link"], ...] = Field(default=("title", "body", "event_at", "link"), min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_fields(self) -> Self:
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("Layout fields must be unique approved references")
        return self
