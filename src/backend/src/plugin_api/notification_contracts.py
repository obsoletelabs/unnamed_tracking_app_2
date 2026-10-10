"""Bounded public notification declarations and event facts for Plugin API v1."""

from string import Formatter
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from .base_contracts import ContractModel
from .ui_contracts import UiField


class NotificationDestinationDeclaration(ContractModel):
    """Enrollment choices; independent core proof determines concrete endpoint trust."""

    kind: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    label: str = Field(min_length=1, max_length=128)
    privacy: Literal["PUBLIC", "PRIVATE"] = "PUBLIC"
    fields: tuple[UiField, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def bounded_fields(self) -> Self:
        validate_configuration_fields(self.fields)
        return self


def validate_configuration_fields(fields: tuple[UiField, ...]) -> None:
    """Reuse field semantics with bounded metadata and no executable regex validation."""
    if len({field.id for field in fields}) != len(fields):
        raise ValueError("Configuration fields require unique IDs")
    for field in fields:
        if len(field.options) > 64:
            raise ValueError("Configuration choices exceed their bounds")
        if field.validation and field.validation.pattern:
            raise ValueError("Provider configuration supports bounds and choices, not patterns")
        if isinstance(field.default, str) and len(field.default) > 4096:
            raise ValueError("Configuration default exceeds its bounds")
        if isinstance(field.default, tuple) and len(field.default) > 64:
            raise ValueError("Configuration default exceeds its bounds")


class NotificationProviderFeatures(ContractModel):
    """Supported delivery behavior; declarations never confer grants or verification."""

    critical_supported: StrictBool = False
    critical_description: str = Field(default="", max_length=500)
    multiple_destinations: StrictBool = True

    @model_validator(mode="after")
    def describe_critical(self) -> Self:
        if self.critical_supported and not str(self.critical_description).strip():
            raise ValueError("Critical support requires an explanation of its behavior")
        return self


class NotificationProviderDefinition(ContractModel):
    """Generic plugin-owned configuration and operations, without credential values."""

    destinations: tuple[NotificationDestinationDeclaration, ...] = Field(min_length=1, max_length=8)
    server_fields: tuple[UiField, ...] = Field(default=(), max_length=32)
    configure_action: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    retire_action: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    test_action: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )
    features: NotificationProviderFeatures = Field(default_factory=NotificationProviderFeatures)

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        if len({destination.kind for destination in self.destinations}) != len(self.destinations):
            raise ValueError("Destination kinds require unique IDs")
        validate_configuration_fields(self.server_fields)
        return self


class NotificationProviderRegistration(ContractModel):
    """Versioned registration preserving existing transports and adding generic plugin delivery."""

    provider_id: str = Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=128)
    action_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    transport: Literal["legacy", "discord_webhook", "plugin"] = "legacy"
    definition: NotificationProviderDefinition | None = None

    @model_validator(mode="after")
    def require_generic_definition(self) -> Self:
        if (self.transport == "plugin") != (self.definition is not None):
            raise ValueError("Only generic plugin transport requires a provider definition")
        return self


class PluginNotificationDestination(ContractModel):
    """Opaque core-generated endpoint authority; contains no account or credential data."""

    id: UUID
    revision: StrictInt = Field(ge=1)
    kind: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9][a-z0-9._-]*$")


class PluginNotificationContent(ContractModel):
    """Approved projection, not the canonical notification or user/entity records."""

    notification_id: UUID
    event_type: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=10_000)
    event_at: StrictInt = Field(ge=0)
    urgency: Literal["normal", "critical"] = "normal"
    link: str | None = Field(default=None, max_length=2048)


class NotificationLifecycleQuery(ContractModel):
    cursor: str | None = Field(default=None, min_length=1, max_length=1024)
    limit: int = Field(default=100, ge=1, le=200, strict=True)


class NotificationLifecycleEvent(ContractModel):
    id: UUID
    notification_id: UUID
    delivery_id: UUID | None = None
    status: Literal[
        "created",
        "read",
        "unread",
        "dismissed",
        "deleted",
        "expired",
        "pending",
        "processing",
        "sent",
        "retry_wait",
        "failed_permanent",
        "suppressed",
        "cancelled",
    ]
    occurred_at: str


class NotificationLifecyclePage(ContractModel):
    events: tuple[NotificationLifecycleEvent, ...]
    cursor: str
    has_more: bool
    resync_required: bool


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
    parameters: dict[str, Literal["string", "integer", "boolean"]] = Field(
        default_factory=dict, max_length=32
    )

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
    fields: tuple[Literal["title", "body", "event_at", "link"], ...] = Field(
        default=("title", "body", "event_at", "link"), min_length=1, max_length=4
    )

    @model_validator(mode="after")
    def unique_fields(self) -> Self:
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("Layout fields must be unique approved references")
        return self
