"""Canonical notification event, story, content and capability contract (version 1).

These public wire models describe what producers submit and what renderers and
providers may receive. They deliberately contain no recipient, destination,
trust, purpose, grant, installation or delivery-state fields: the host derives
those from authenticated context and its own records. The normative rules are
documented in ``wiki/docs/development/notification-contract.md``.
"""

import ipaddress
import re
from string import Formatter
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AfterValidator, Field, StrictBool, StrictInt, StrictStr, model_validator

from .base_contracts import ContractModel, parse_semver

NOTIFICATION_CONTRACT_VERSION = "1.0.0"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
MAX_SAFE_INTEGER = 2**53 - 1
MAX_CONTENT_CHARACTERS = 48_000

LINE_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
TEXT_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TOP_LEVEL_LABEL = re.compile(r"^(?:[a-z][a-z0-9-]*[a-z0-9]|xn--[a-z0-9-]+)$")
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa", ".localdomain")
_APP_PATH = re.compile(r"^/(?!/)[A-Za-z0-9._~!$&'()*+,;=:@/?-]*$")
_DECIMAL = r"^-?(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,6})?$"
_AMOUNT = r"^(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,6})?$"


def notification_contract_reason(
    declared: str, host: str = NOTIFICATION_CONTRACT_VERSION
) -> str | None:
    """Explain why a declared document version is unsupported, or return None."""
    try:
        version = parse_semver(declared)
    except ValueError:
        return f"Notification contract version {declared!r} is not a semantic version."
    supported = parse_semver(host)
    if version[0] != supported[0]:
        return f"Notification contract {declared} has an unsupported major version ({host})."
    if version > supported:
        return f"Notification contract {declared} is newer than this host ({host})."
    return None


def negotiate_notification_contract(
    consumer_version: str | None, host: str = NOTIFICATION_CONTRACT_VERSION
) -> str | None:
    """Select the version a renderer receives; None selects the legacy title/body adapter."""
    if consumer_version is None:
        return None
    try:
        consumer = parse_semver(consumer_version)
    except ValueError:
        return None
    supported = parse_semver(host)
    if consumer[0] != supported[0]:
        return None
    return ".".join(str(part) for part in min(consumer, supported))


def _supported_version(value: str) -> str:
    reason = notification_contract_reason(value)
    if reason:
        raise ValueError(reason)
    return value


def _single_line(value: str) -> str:
    if LINE_CONTROL_CHARACTERS.search(value):
        raise ValueError("Text must be one line without control characters")
    return value


def _multi_line(value: str) -> str:
    if TEXT_CONTROL_CHARACTERS.search(value):
        raise ValueError("Text may contain line breaks and tabs, not other control characters")
    return value


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Text must not be blank")
    return value


def _public_https_url(value: str) -> str:
    """Accept only credential-free public HTTPS names; the host may still proxy or drop it."""
    if (
        len(value) > 2048
        or LINE_CONTROL_CHARACTERS.search(value)
        or any(character.isspace() or character == "\\" for character in value)
        or not value.isascii()
    ):
        raise ValueError("URL contains unsupported characters")
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("URL port is invalid") from exc
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("Use an HTTPS URL without credentials or a custom port")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("URL hosts must be public DNS names, not IP addresses")
    labels = host.split(".")
    if (
        len(labels) < 2
        or host.endswith(_LOCAL_SUFFIXES)
        or not all(_DNS_LABEL.fullmatch(label) for label in labels)
        or not _TOP_LEVEL_LABEL.fullmatch(labels[-1])
    ):
        raise ValueError("URL hosts must be public DNS names")
    return value


def _app_path(value: str) -> str:
    """Host-relative application route, resolved by the host's notification URL precedence."""
    path = value.split("?", 1)[0]
    if (
        len(value) > 512
        or not _APP_PATH.fullmatch(value)
        or ".." in path.split("/")
        or path == "/api"
        or path.startswith("/api/")
    ):
        raise ValueError("Use an application path such as /plugins/example, not an API route")
    return value


PlainLine = Annotated[StrictStr, AfterValidator(_single_line)]
DisplayLine = Annotated[StrictStr, AfterValidator(_single_line), AfterValidator(_nonblank)]
DisplayText = Annotated[StrictStr, AfterValidator(_multi_line), AfterValidator(_nonblank)]
ContractVersion = Annotated[StrictStr, AfterValidator(_supported_version)]
PublicHttpsUrl = Annotated[StrictStr, AfterValidator(_public_https_url)]
ApplicationPath = Annotated[StrictStr, AfterValidator(_app_path)]
Identifier = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
FactKey = Annotated[StrictStr, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")]
EventType = Annotated[
    StrictStr, Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
]
Severity = Literal["info", "warning", "error"]


class TextBlock(ContractModel):
    """Plain text paragraph(s); renderers escape it for every surface."""

    type: Literal["text"]
    text: DisplayText = Field(min_length=1, max_length=10_000)


class MarkdownBlock(ContractModel):
    """CommonMark subset; raw HTML is shown as text and only https/app links are linked."""

    type: Literal["markdown"]
    markdown: DisplayText = Field(min_length=1, max_length=10_000)


ContentBlock = Annotated[TextBlock | MarkdownBlock, Field(discriminator="type")]


class TextFactValue(ContractModel):
    kind: Literal["text"]
    value: PlainLine = Field(max_length=1000)


class IntegerFactValue(ContractModel):
    kind: Literal["integer"]
    value: StrictInt = Field(ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)


class DecimalFactValue(ContractModel):
    """Exact decimal strings; binary floating point is never used for displayed facts."""

    kind: Literal["decimal"]
    value: StrictStr = Field(pattern=_DECIMAL)


class MoneyFactValue(ContractModel):
    kind: Literal["money"]
    amount: StrictStr = Field(pattern=_AMOUNT)
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")


class TimestampFactValue(ContractModel):
    """Unix seconds; renderers format it in the recipient's locale/time zone."""

    kind: Literal["timestamp"]
    value: StrictInt = Field(ge=0, le=MAX_SAFE_INTEGER)


class BooleanFactValue(ContractModel):
    kind: Literal["boolean"]
    value: StrictBool


FactValue = Annotated[
    TextFactValue
    | IntegerFactValue
    | DecimalFactValue
    | MoneyFactValue
    | TimestampFactValue
    | BooleanFactValue,
    Field(discriminator="kind"),
]


class ContentFact(ContractModel):
    key: FactKey
    label: DisplayLine = Field(min_length=1, max_length=64)
    value: FactValue


class HostMediaImage(ContractModel):
    """Host-resolved artwork for a record the recipient owns; unresolvable references drop."""

    kind: Literal["host_media"]
    media_type: Identifier
    media_id: UUID


class ExternalImage(ContractModel):
    """Public image URL; host policy chooses direct use, proxy/cache or fallback artwork."""

    kind: Literal["external"]
    url: PublicHttpsUrl


class ContentImage(ContractModel):
    role: Literal["artwork", "thumbnail", "hero", "icon"] = "artwork"
    source: Annotated[HostMediaImage | ExternalImage, Field(discriminator="kind")]
    alt: PlainLine = Field(default="", max_length=300)


class LinkTarget(ContractModel):
    kind: Literal["link"]
    url: PublicHttpsUrl


class HostRouteTarget(ContractModel):
    kind: Literal["host_route"]
    path: ApplicationPath


class PluginActionTarget(ContractModel):
    """A declared action invoked only through a host-authorised route after revalidation."""

    kind: Literal["plugin_action"]
    action_id: StrictStr = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    input: dict[Identifier, StrictStr | StrictInt | StrictBool] = Field(
        default_factory=dict, max_length=8
    )

    @model_validator(mode="after")
    def bounded_input(self) -> Self:
        for value in dict(self.input).values():
            if isinstance(value, str) and (
                len(value) > 500 or LINE_CONTROL_CHARACTERS.search(value)
            ):
                raise ValueError("Action input strings must be short single lines")
            if isinstance(value, int) and abs(value) > MAX_SAFE_INTEGER:
                raise ValueError("Action input integers exceed their bounds")
        return self


class ContentAction(ContractModel):
    id: Identifier
    label: DisplayLine = Field(min_length=1, max_length=40)
    style: Literal["default", "primary", "danger"] = "default"
    target: Annotated[
        LinkTarget | HostRouteTarget | PluginActionTarget, Field(discriminator="kind")
    ]


class ContentAttribution(ContractModel):
    """Display attribution only; the verified source always comes from host records."""

    label: DisplayLine = Field(min_length=1, max_length=80)
    url: PublicHttpsUrl | None = None


class NotificationContent(ContractModel):
    """Canonical PRIVATE content for one story revision; projections are separate host copies."""

    title: DisplayLine = Field(min_length=1, max_length=500)
    summary: DisplayText = Field(min_length=1, max_length=10_000)
    blocks: tuple[ContentBlock, ...] = Field(default=(), max_length=32)
    facts: tuple[ContentFact, ...] = Field(default=(), max_length=24)
    images: tuple[ContentImage, ...] = Field(default=(), max_length=4)
    actions: tuple[ContentAction, ...] = Field(default=(), max_length=5)
    attribution: ContentAttribution | None = None
    expires_at: StrictInt | None = Field(default=None, ge=0, le=MAX_SAFE_INTEGER)

    @model_validator(mode="after")
    def bounded_content(self) -> Self:
        if len({fact.key for fact in self.facts}) != len(self.facts):
            raise ValueError("Fact keys must be unique")
        if len({action.id for action in self.actions}) != len(self.actions):
            raise ValueError("Action IDs must be unique")
        if content_characters(self) > MAX_CONTENT_CHARACTERS:
            raise ValueError("Notification content exceeds its total text bound")
        return self


def content_characters(content: NotificationContent) -> int:
    """Count every displayed string so many small fields cannot exceed one bound."""
    total = len(content.title) + len(content.summary)
    for block in content.blocks:
        total += len(block.text if isinstance(block, TextBlock) else block.markdown)
    for fact in content.facts:
        total += len(fact.label) + (
            len(fact.value.value) if isinstance(fact.value, TextFactValue) else 0
        )
    total += sum(len(image.alt) for image in content.images)
    total += sum(len(action.label) for action in content.actions)
    return total + (len(content.attribution.label) if content.attribution else 0)


class StoryReference(ContractModel):
    """Producer-stable story identity plus the lifecycle phase this event asserts."""

    key: DisplayLine = Field(min_length=1, max_length=128)
    phase: Identifier


class CanonicalNotificationEvent(ContractModel):
    """One producer occurrence. The gateway supplies recipient, source and installation."""

    contract_version: ContractVersion
    event_type: EventType
    event_id: StrictStr = Field(min_length=1, max_length=64)
    occurred_at: StrictInt = Field(ge=0, le=MAX_SAFE_INTEGER)
    story: StoryReference | None = None
    grouping_key: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    urgency: Literal["normal", "critical"] = "normal"
    content: NotificationContent

    @model_validator(mode="after")
    def expiry_follows_occurrence(self) -> Self:
        if self.content.expires_at is not None and self.content.expires_at <= self.occurred_at:
            raise ValueError("Content expiry must follow the event occurrence")
        return self


class StoryPhase(ContractModel):
    id: Identifier
    label: DisplayLine = Field(min_length=1, max_length=64)
    terminal: StrictBool = False
    severity: Severity | None = None


class StoryTransition(ContractModel):
    from_phase: Identifier
    to_phase: Identifier


class StoryLifecycleDeclaration(ContractModel):
    """Declared phases for one event type; self-transitions allow in-phase content updates."""

    contract_version: ContractVersion
    event_type: EventType
    phases: tuple[StoryPhase, ...] = Field(min_length=1, max_length=16)
    initial_phases: tuple[Identifier, ...] = Field(min_length=1, max_length=16)
    transitions: tuple[StoryTransition, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def consistent_graph(self) -> Self:
        phases = {phase.id: phase for phase in self.phases}
        if len(phases) != len(self.phases):
            raise ValueError("Story phase IDs must be unique")
        if len(set(self.initial_phases)) != len(self.initial_phases) or not set(
            self.initial_phases
        ).issubset(phases):
            raise ValueError("Initial phases must be unique declared phases")
        edges = [(edge.from_phase, edge.to_phase) for edge in self.transitions]
        if len(set(edges)) != len(edges):
            raise ValueError("Story transitions must be unique")
        if any(source not in phases or target not in phases for source, target in edges):
            raise ValueError("Story transitions must reference declared phases")
        if any(phases[source].terminal for source, _ in edges):
            raise ValueError("Terminal phases cannot transition")
        reachable, pending = set(self.initial_phases), list(self.initial_phases)
        while pending:
            current = pending.pop()
            for source, target in edges:
                if source == current and target not in reachable:
                    reachable.add(target)
                    pending.append(target)
        if reachable != set(phases):
            raise ValueError("Every story phase must be reachable from an initial phase")
        return self

    def phase(self, phase_id: str) -> StoryPhase | None:
        return next((phase for phase in self.phases if phase.id == phase_id), None)

    def allows(self, current: str, target: str) -> bool:
        return any(
            edge.from_phase == current and edge.to_phase == target for edge in self.transitions
        )


class ImageCapabilities(ContractModel):
    max_images: StrictInt = Field(ge=1, le=10)
    external_urls: StrictBool = False
    proxied_urls: StrictBool = False

    @model_validator(mode="after")
    def some_source(self) -> Self:
        if not (self.external_urls or self.proxied_urls):
            raise ValueError("Image support requires external or host-proxied URLs")
        return self


class NotificationProviderCapabilities(ContractModel):
    """What a renderer can present. Declarations never grant trust, proof, consent or grants."""

    contract_version: ContractVersion
    max_batch_items: StrictInt = Field(default=1, ge=1, le=50)
    message_updates: Literal["none", "edit", "replace"] = "none"
    threaded_follow_ups: StrictBool = False
    text_formats: tuple[Literal["plain", "markdown", "html_email"], ...] = Field(
        default=("plain",), min_length=1, max_length=3
    )
    rich_embeds: StrictBool = False
    images: ImageCapabilities | None = None
    action_buttons: StrictInt = Field(default=0, ge=0, le=10)
    links: StrictBool = False
    live_page_links: StrictBool = False
    max_title_chars: StrictInt = Field(default=500, ge=16, le=500)
    max_text_chars: StrictInt = Field(default=10_000, ge=64, le=100_000)
    max_payload_bytes: StrictInt = Field(default=65_536, ge=1024, le=10_485_760)

    @model_validator(mode="after")
    def coherent_features(self) -> Self:
        if "plain" not in self.text_formats or len(set(self.text_formats)) != len(
            self.text_formats
        ):
            raise ValueError("Text formats must be unique and include plain text")
        if (self.live_page_links or self.action_buttons) and not self.links:
            raise ValueError("Live-page links and action buttons require link support")
        return self


TEMPLATE_PLACEHOLDERS = frozenset(
    {
        "title",
        "summary",
        "blocks",
        "facts",
        "actions",
        "attribution",
        "occurred_at",
        "expires_at",
        "live_link",
    }
)
_FACT_PLACEHOLDER = re.compile(r"^fact_[A-Za-z_][A-Za-z0-9_]{0,31}$")
# Early rejection for authors; the host allowlist sanitiser remains the enforcement point.
_EXECUTABLE_MARKUP = re.compile(
    r"<\s*/?\s*(?:script|iframe|frame|frameset|object|embed|applet|form|input|button|textarea"
    r"|select|base|meta|link|svg|math|template|noscript)\b"
    r"|[\s/\"']on[a-z]+\s*="
    r"|=\s*[\"']?\s*(?:javascript|vbscript|data)\s*:"
    r"|expression\s*\(|@import",
    re.IGNORECASE,
)
ContributionField = Literal[
    "title",
    "summary",
    "blocks",
    "facts",
    "images",
    "actions",
    "attribution",
    "occurred_at",
    "expires_at",
    "live_link",
]


class RendererContribution(ContractModel):
    """Presentation for one of the producer's own types; it never adds data or authority."""

    contract_version: ContractVersion
    event_type: EventType
    surface: Literal["card", "email", "chat", "push", "text"]
    kind: Literal["fields", "markdown_template", "html_template"]
    fields: tuple[ContributionField, ...] = Field(default=(), max_length=10)
    template: StrictStr | None = Field(default=None, min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def valid_presentation(self) -> Self:
        if self.kind == "fields":
            if not self.fields or self.template is not None:
                raise ValueError("Field layouts require field references and no template")
            if len(set(self.fields)) != len(self.fields):
                raise ValueError("Field references must be unique")
            return self
        if self.template is None or self.fields:
            raise ValueError("Template contributions require a template and no field list")
        if self.kind == "html_template" and self.surface != "email":
            raise ValueError("HTML templates are limited to the email surface")
        _multi_line(self.template)
        try:
            placeholders = list(Formatter().parse(self.template))
        except ValueError as exc:
            raise ValueError("Template braces are unbalanced") from exc
        for _, name, spec, conversion in placeholders:
            if name is None:
                continue
            if (
                spec
                or conversion
                or (name not in TEMPLATE_PLACEHOLDERS and not _FACT_PLACEHOLDER.fullmatch(name))
            ):
                raise ValueError("Templates support only named canonical content placeholders")
        if self.kind == "html_template" and _EXECUTABLE_MARKUP.search(self.template):
            raise ValueError("HTML templates cannot contain executable or embedded markup")
        return self


NOTIFICATION_CONTRACT_DOCUMENTS: dict[str, type[ContractModel]] = {
    "notification-canonical-event-v1": CanonicalNotificationEvent,
    "notification-canonical-content-v1": NotificationContent,
    "notification-story-lifecycle-v1": StoryLifecycleDeclaration,
    "notification-provider-capabilities-v1": NotificationProviderCapabilities,
    "notification-renderer-contribution-v1": RendererContribution,
}


def notification_contract_schemas() -> dict[str, dict[str, Any]]:
    """Public JSON Schemas for SDKs; semantic rules above remain normative."""
    schemas = {}
    for name, model in NOTIFICATION_CONTRACT_DOCUMENTS.items():
        schema = model.model_json_schema()
        schema["$schema"] = JSON_SCHEMA_DIALECT
        schema["x-notification-contract-version"] = NOTIFICATION_CONTRACT_VERSION
        schemas[name] = schema
    return schemas
