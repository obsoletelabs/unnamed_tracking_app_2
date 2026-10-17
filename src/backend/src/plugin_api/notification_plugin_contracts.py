"""Plugin-facing registration of canonical notification types (Plugin API 1.1.14).

A registration lets a plugin declare, for one of its own event types, the structured facts it will
send, the story lifecycle and stable-key rules, the grouping it wants, the actions it offers, an
urgency ceiling and presentation contributions. It carries no recipient, destination, credential,
trust-promotion or delivery-state field: the host derives those from authenticated context and its
own records, and validates every published event against the registration
(:func:`check_event_against_registration`). The normative description is in
``wiki/docs/development/notification-contract.md``.
"""

from __future__ import annotations

from string import Formatter
from typing import Any, Literal, Self

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from .base_contracts import Capability, ContractModel
from .notification_contracts import PluginCanonicalDelivery
from .notification_story_contracts import (
    JSON_SCHEMA_DIALECT,
    NOTIFICATION_CONTRACT_VERSION,
    BooleanFactValue,
    CanonicalNotificationEvent,
    ContractVersion,
    DecimalFactValue,
    DisplayLine,
    EventType,
    FactKey,
    Identifier,
    IntegerFactValue,
    MoneyFactValue,
    NotificationContent,
    PluginActionTarget,
    RendererContribution,
    Severity,
    StoryLifecycleDeclaration,
    TextFactValue,
    TimestampFactValue,
)

NOTIFICATION_PLUGIN_API_VERSION = "1.1.14"
FactKind = Literal["text", "integer", "decimal", "money", "timestamp", "boolean"]
ActionValueType = Literal["string", "integer", "boolean"]
# Capabilities an action may require of its plugin installation. Notification authority itself and
# the whole-API grant are never delegated to an action.
_FORBIDDEN_ACTION_REQUIREMENTS = frozenset(
    {Capability.FULL_API.value, Capability.NOTIFICATIONS_SENSITIVE.value}
)
_FACT_VALUE_TYPES = {
    "text": TextFactValue,
    "integer": IntegerFactValue,
    "decimal": DecimalFactValue,
    "money": MoneyFactValue,
    "timestamp": TimestampFactValue,
    "boolean": BooleanFactValue,
}


class FactDeclaration(ContractModel):
    """One structured fact an event type may carry; the kind is fixed so renderers can format it."""

    key: FactKey
    label: DisplayLine = Field(min_length=1, max_length=64)
    kind: FactKind
    required: StrictBool = True


class ActionInputDeclaration(ContractModel):
    type: ActionValueType
    required: StrictBool = True
    max_length: StrictInt = Field(default=500, ge=1, le=500)


class ActionDeclaration(ContractModel):
    """A typed, host-authorised action. Execution always reruns the host's checks."""

    action_id: StrictStr = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    label: DisplayLine = Field(min_length=1, max_length=40)
    input: dict[Identifier, ActionInputDeclaration] = Field(default_factory=dict, max_length=8)
    output: Literal["none", "message"] = "message"
    requires: tuple[StrictStr, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def known_requirements(self) -> Self:
        if len(set(self.requires)) != len(self.requires):
            raise ValueError("Action capability requirements must be unique")
        for name in self.requires:
            try:
                Capability(name)
            except ValueError as exc:
                raise ValueError(f"Unsupported capability requirement: {name[:64]}") from exc
            if name in _FORBIDDEN_ACTION_REQUIREMENTS:
                raise ValueError("Actions cannot require sensitive or whole-API authority")
        return self


class GroupingDeclaration(ContractModel):
    """``key`` events carry a grouping key; ``separate`` asks the host not to merge into digests.

    Aggregation timing, windows and digests stay host policy (and user preference).
    """

    strategy: Literal["none", "key"] = "none"
    merge: Literal["digest", "separate"] = "digest"


class CanonicalNotificationTypeRegistration(ContractModel):
    """Versioned declaration of one canonical event type owned by the registering plugin."""

    contract_version: ContractVersion
    event_type: EventType
    label: DisplayLine = Field(min_length=1, max_length=128)
    description: StrictStr = Field(default="", max_length=500)
    required_trust: Literal["PRIVATE", "SECURE"] = "PRIVATE"
    purpose: Literal["standard", "security"] = "standard"
    severity: Severity = "info"
    # Categories are host-assigned: plugin producers always belong to "plugin".
    default_category: Literal["plugin"] = "plugin"
    max_urgency: Literal["normal", "critical"] = "normal"
    lifecycle: StoryLifecycleDeclaration
    story_key_prefix: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    grouping: GroupingDeclaration = Field(default_factory=GroupingDeclaration)
    facts: tuple[FactDeclaration, ...] = Field(default=(), max_length=24)
    actions: tuple[ActionDeclaration, ...] = Field(default=(), max_length=5)
    renderers: tuple[RendererContribution, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.purpose != "standard" and self.required_trust != "SECURE":
            raise ValueError("Security notification types require SECURE destinations")
        if self.lifecycle.event_type != self.event_type:
            raise ValueError("The lifecycle describes another event type")
        if len({fact.key for fact in self.facts}) != len(self.facts):
            raise ValueError("Fact keys must be unique")
        if len({action.action_id for action in self.actions}) != len(self.actions):
            raise ValueError("Action IDs must be unique")
        presentations = [(item.surface, item.kind) for item in self.renderers]
        if len(set(presentations)) != len(presentations):
            raise ValueError("Each surface and kind may have one renderer contribution")
        if any(item.event_type != self.event_type for item in self.renderers):
            raise ValueError("Renderer contributions must describe this event type")
        declared = {fact.key for fact in self.facts}
        for item in self.renderers:
            if item.template is None:
                continue
            for _, name, _spec, _conv in Formatter().parse(item.template):
                if name and name.startswith("fact_") and name[5:] not in declared:
                    raise ValueError("Templates may only reference declared facts")
        return self

    def action(self, action_id: str) -> ActionDeclaration | None:
        return next((item for item in self.actions if item.action_id == action_id), None)

    @property
    def needs_html_grant(self) -> bool:
        return any(item.kind == "html_template" for item in self.renderers)


class ContractNegotiationRequest(ContractModel):
    """What a plugin SDK supports; the host answers with the version both sides will use."""

    contract_version: StrictStr = Field(min_length=1, max_length=32)


class ContractNegotiationResult(ContractModel):
    host_contract_version: str
    plugin_api_version: str
    negotiated: str | None
    reason: str | None = None
    supported_major: int
    features: tuple[str, ...]


NEGOTIATED_FEATURES: tuple[str, ...] = (
    "canonical_events",
    "story_lifecycles",
    "structured_facts",
    "render_contributions",
    "typed_actions",
    "urgency_requests",
    "provider_capabilities",
    "message_edits",
)


def _fact_matches(kind: str, value: object) -> bool:
    return isinstance(value, _FACT_VALUE_TYPES[kind])


def _check_facts(
    registration: CanonicalNotificationTypeRegistration, content: NotificationContent
) -> None:
    declared = {fact.key: fact for fact in registration.facts}
    sent = {fact.key: fact for fact in content.facts}
    if unknown := sorted(set(sent) - set(declared)):
        raise ValueError(f"Facts are not declared for this event type: {unknown[0]}")
    if missing := sorted(
        key for key, fact in declared.items() if fact.required and key not in sent
    ):
        raise ValueError(f"Required fact is missing: {missing[0]}")
    for key, fact in sent.items():
        if not _fact_matches(declared[key].kind, fact.value):
            raise ValueError(f"Fact {key} must be of kind {declared[key].kind}")


def check_action_target(
    registration: CanonicalNotificationTypeRegistration, target: PluginActionTarget
) -> ActionDeclaration:
    """The declaration a plugin action target must satisfy, or ValueError."""
    declaration = registration.action(target.action_id)
    if declaration is None:
        raise ValueError(f"Action is not declared for this event type: {target.action_id}")
    values = dict(target.input)
    if unknown := sorted(set(values) - set(declaration.input)):
        raise ValueError(f"Action input is not declared: {unknown[0]}")
    for name, spec in declaration.input.items():
        if name not in values:
            if spec.required:
                raise ValueError(f"Action input is required: {name}")
            continue
        value = values[name]
        expected = {"string": str, "integer": int, "boolean": bool}[spec.type]
        if not isinstance(value, expected) or (spec.type == "integer" and isinstance(value, bool)):
            raise ValueError(f"Action input {name} must be of type {spec.type}")
        if isinstance(value, str) and len(value) > spec.max_length:
            raise ValueError(f"Action input {name} exceeds its declared length")
    return declaration


def _check_actions(
    registration: CanonicalNotificationTypeRegistration, content: NotificationContent
) -> None:
    for action in content.actions:
        if isinstance(action.target, PluginActionTarget):
            check_action_target(registration, action.target)


def check_event_against_registration(
    registration: CanonicalNotificationTypeRegistration, event: CanonicalNotificationEvent
) -> None:
    """Raise ValueError unless the event conforms to the plugin's own declaration.

    Transition legality against the story's current phase is decided later by the host under a
    row lock; this pure check covers everything the declaration alone determines.
    """
    if event.event_type != registration.event_type:
        raise ValueError("The event describes another event type")
    if event.story is None:
        raise ValueError("Canonical plugin events belong to a story")
    prefix = registration.story_key_prefix
    if prefix is not None and not event.story.key.startswith(prefix):
        raise ValueError("The story key does not use the declared prefix")
    if registration.lifecycle.phase(event.story.phase) is None:
        raise ValueError("The story phase is not declared")
    if event.urgency == "critical" and registration.max_urgency != "critical":
        raise ValueError("The event type does not declare critical urgency")
    if registration.grouping.strategy == "none" and event.grouping_key is not None:
        raise ValueError("The event type declares no grouping")
    if registration.grouping.strategy == "key" and event.grouping_key is None:
        raise ValueError("The event type declares grouping and needs a grouping key")
    _check_facts(registration, event.content)
    _check_actions(registration, event.content)


PLUGIN_NOTIFICATION_DOCUMENTS: dict[str, type[ContractModel]] = {
    "notification-canonical-type-v1": CanonicalNotificationTypeRegistration,
    "notification-canonical-delivery-v1": PluginCanonicalDelivery,
    "notification-contract-negotiation-request-v1": ContractNegotiationRequest,
    "notification-contract-negotiation-result-v1": ContractNegotiationResult,
}


def plugin_notification_schemas() -> dict[str, dict[str, Any]]:
    """JSON Schemas of the Plugin API 1.1.14 documents; semantic rules above stay normative."""
    schemas = {}
    for name, model in PLUGIN_NOTIFICATION_DOCUMENTS.items():
        schema = model.model_json_schema()
        schema["$schema"] = JSON_SCHEMA_DIALECT
        schema["x-api-contract-version"] = NOTIFICATION_PLUGIN_API_VERSION
        schema["x-notification-contract-version"] = NOTIFICATION_CONTRACT_VERSION
        schemas[name] = schema
    return schemas
