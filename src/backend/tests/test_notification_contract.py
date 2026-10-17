"""Canonical notification contract: schemas, versions, lifecycle matrix and public boundary."""

import ast
import json
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

import src.plugin_api as public_api
from src.features import notification_contract as contract
from src.features.notification_contract import (
    INBOX_OPERATIONS,
    ItemState,
    RevisionDecision,
    StoryHead,
    decide_revision,
    item_state,
    reconciliation_operation,
    single_item_lifecycle,
)
from src.plugin_api.notification_contracts import NotificationLifecycleEvent
from src.plugin_api.notification_story_contracts import (
    MAX_CONTENT_CHARACTERS,
    NOTIFICATION_CONTRACT_DOCUMENTS,
    NOTIFICATION_CONTRACT_VERSION,
    CanonicalNotificationEvent,
    ContentAction,
    ExternalImage,
    NotificationContent,
    NotificationProviderCapabilities,
    RendererContribution,
    StoryLifecycleDeclaration,
    negotiate_notification_contract,
    notification_contract_reason,
    notification_contract_schemas,
)

FIXTURES = Path(__file__).parent / "fixtures" / "notification_contract"
CONFORMANCE = json.loads((FIXTURES / "conformance.json").read_text(encoding="utf-8"))
NOW = 1_767_225_600


def case(name):
    return next(item["document"] for item in CONFORMANCE["cases"] if item["name"] == name)


PLUGIN_UPDATES = StoryLifecycleDeclaration.model_validate(case("reference plugin-update lifecycle"))


@pytest.mark.parametrize(
    "item", CONFORMANCE["cases"], ids=[item["name"] for item in CONFORMANCE["cases"]]
)
def test_shared_conformance_cases(item):
    """The same JSON fixtures are the companion SDK's validation contract."""
    model = NOTIFICATION_CONTRACT_DOCUMENTS[item["document_type"]]
    if item["valid"]:
        parsed = model.model_validate(item["document"])
        # Round trips are stable, so SDKs can compare documents after normalisation.
        assert model.model_validate_json(parsed.model_dump_json()) == parsed
        return
    with pytest.raises(ValidationError) as failure:
        model.model_validate(item["document"])
    # Each invalid fixture isolates one rule rather than failing incidentally.
    assert len(failure.value.errors()) == 1


def test_conformance_fixture_covers_every_document_type():
    assert CONFORMANCE["contract_version"] == NOTIFICATION_CONTRACT_VERSION
    for name in NOTIFICATION_CONTRACT_DOCUMENTS:
        outcomes = {item["valid"] for item in CONFORMANCE["cases"] if item["document_type"] == name}
        if name != "notification-canonical-content-v1":
            assert outcomes == {True, False}, name


@pytest.mark.parametrize("name", sorted(NOTIFICATION_CONTRACT_DOCUMENTS))
def test_published_schemas_match_reviewed_snapshots(name):
    """Schema changes must be deliberate; companion repositories pin these files."""
    schema = notification_contract_schemas()[name]
    snapshot = json.loads((FIXTURES / "schemas" / f"{name}.schema.json").read_text("utf-8"))
    assert schema == snapshot
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["x-notification-contract-version"] == NOTIFICATION_CONTRACT_VERSION
    assert schema["additionalProperties"] is False
    for definition in schema.get("$defs", {}).values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False


def test_snapshot_directory_has_no_stale_schemas():
    published = {path.name for path in (FIXTURES / "schemas").glob("*.schema.json")}
    assert published == {f"{name}.schema.json" for name in NOTIFICATION_CONTRACT_DOCUMENTS}


@pytest.mark.parametrize(
    "declared,reason",
    [
        ("1.0.0", None),
        ("1.0.1", "newer"),
        ("1.1.0", "newer"),
        ("0.9.0", "major"),
        ("2.0.0", "major"),
        ("1.0", "semantic"),
        ("v1.0.0", "semantic"),
        ("01.0.0", "semantic"),
    ],
)
def test_version_compatibility_rules(declared, reason):
    outcome = notification_contract_reason(declared)
    assert outcome is None if reason is None else reason in outcome
    assert notification_contract_reason("1.1.0", host="1.2.0") is None


@pytest.mark.parametrize(
    "consumer,host,selected",
    [
        (None, "1.0.0", None),
        ("1.0.0", "1.0.0", "1.0.0"),
        ("1.4.2", "1.0.0", "1.0.0"),
        ("1.1.0", "1.3.0", "1.1.0"),
        ("2.0.0", "1.0.0", None),
        ("0.9.0", "1.0.0", None),
        ("latest", "1.0.0", None),
    ],
)
def test_renderer_version_negotiation_falls_back_to_legacy(consumer, host, selected):
    assert negotiate_notification_contract(consumer, host) == selected


def event(**changes):
    return {**case("rich event"), **changes}


def content(**changes):
    return {**case("rich event")["content"], **changes}


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "recipient",
        "destination_id",
        "provider_id",
        "installation_id",
        "source",
        "trust",
        "required_trust",
        "purpose",
        "severity",
        "verified",
        "delivery_status",
        "attempt_id",
        "credentials",
        "html",
    ],
)
def test_producers_cannot_assert_host_authority(field):
    with pytest.raises(ValidationError, match="Extra inputs"):
        CanonicalNotificationEvent.model_validate(event(**{field: "PUBLIC"}))
    with pytest.raises(ValidationError, match="Extra inputs"):
        NotificationContent.model_validate(content(**{field: "PUBLIC"}))


@pytest.mark.parametrize(
    "url",
    [
        "https://images.example.org/a.png",
        "https://cdn.example.co.uk:443/path/a.png?size=2",
        "https://xn--bcher-kva.example/a.png",
    ],
)
def test_public_https_urls_are_accepted(url):
    assert ExternalImage(kind="external", url=url).url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://images.example.org/a.png",
        "javascript:alert(1)",
        "data:image/png;base64,AA",
        "file:///etc/passwd",
        "https://user@images.example.org/a.png",
        "https://images.example.org:8443/a.png",
        "https://127.0.0.1/a.png",
        "https://[::1]/a.png",
        "https://10.0.0.5/a.png",
        "https://127.1/a.png",
        "https://2130706433/a.png",
        "https://localhost/a.png",
        "https://jellyfin.local/a.png",
        "https://router.lan/a.png",
        "https://service.internal/a.png",
        "https://b\u00fccher.example/a.png",
        "https://images.example.org/a b.png",
        "https://images.example.org\\@evil.example/a.png",
        "https://-bad-.example.org/a.png",
        "https:///a.png",
        "https://example." + "a" * 2048,
    ],
)
def test_unsafe_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        ExternalImage(kind="external", url=url)
    with pytest.raises(ValidationError):
        ContentAction(id="open", label="Open", target={"kind": "link", "url": url})


@pytest.mark.parametrize(
    "path,allowed",
    [
        ("/", True),
        ("/plugins/example.sync", True),
        ("/settings?area=account&section=notifications", True),
        ("/api", False),
        ("/api/notifications/email-unsubscribe?token=x", False),
        ("//evil.example/path", False),
        ("/plugins/../api/users", False),
        ("/plugins/%2e%2e/api", False),
        ("/plugins#fragment", False),
        ("/plugins\\x", False),
        ("plugins", False),
        ("https://app.example.org/plugins", False),
        ("/" + "a" * 512, False),
    ],
)
def test_host_route_actions_stay_inside_the_application(path, allowed):
    target = {"kind": "host_route", "path": path}
    if allowed:
        assert ContentAction(id="open", label="Open", target=target).target.path == path
    else:
        with pytest.raises(ValidationError):
            ContentAction(id="open", label="Open", target=target)


def test_text_rules_allow_paragraphs_but_not_controls():
    parsed = NotificationContent.model_validate(
        content(summary="First line\n\tindented", blocks=[{"type": "text", "text": "a\nb"}])
    )
    assert parsed.summary == "First line\n\tindented"
    for changes in (
        {"summary": "carriage\rreturn"},
        {"summary": "escape\x1b[31m"},
        {"summary": "next" + chr(0x85) + "line"},
        {"blocks": [{"type": "markdown", "markdown": "  \n "}]},
        {"title": "tab\tin title"},
        {"facts": [{"key": "a", "label": "A", "value": {"kind": "text", "value": "two\nlines"}}]},
    ):
        with pytest.raises(ValidationError):
            NotificationContent.model_validate(content(**changes))


def test_total_content_size_is_bounded_across_fields():
    blocks = [{"type": "text", "text": "x" * 10_000} for _ in range(5)]
    with pytest.raises(ValidationError, match="total text bound"):
        NotificationContent.model_validate(content(blocks=blocks))
    accepted = NotificationContent.model_validate(content(blocks=blocks[:3]))
    assert contract.legacy_text(accepted)[1].endswith("\u2026")
    assert MAX_CONTENT_CHARACTERS == 48_000


@pytest.mark.parametrize(
    "fact",
    [
        {"kind": "decimal", "value": "1.5e3"},
        {"kind": "decimal", "value": "01"},
        {"kind": "decimal", "value": "1.1234567"},
        {"kind": "money", "amount": "-1.00", "currency": "EUR"},
        {"kind": "money", "amount": "1.00", "currency": "eur"},
        {"kind": "integer", "value": 2**53},
        {"kind": "integer", "value": "3"},
        {"kind": "timestamp", "value": -1},
        {"kind": "boolean", "value": "true"},
        {"kind": "text", "value": "x" * 1001},
    ],
)
def test_typed_facts_are_exact_and_bounded(fact):
    with pytest.raises(ValidationError):
        NotificationContent.model_validate(
            content(facts=[{"key": "value", "label": "Value", "value": fact}])
        )


def test_plugin_action_input_is_bounded():
    target = {"kind": "plugin_action", "action_id": "updates.install"}
    for values in (
        {"plugin": "x" * 501},
        {"plugin": "a\nb"},
        {"count": 2**54},
        {"nested": {"x": 1}},
        {"Bad-Key": "x"},
        {f"k{index}": index for index in range(9)},
    ):
        with pytest.raises(ValidationError):
            ContentAction(id="install", label="Install", target={**target, "input": values})


@pytest.mark.parametrize(
    "transitions,initial,error",
    [
        ([{"from_phase": "available", "to_phase": "gone"}], ["available"], "declared phases"),
        (
            [{"from_phase": "available", "to_phase": "installed"}] * 2,
            ["available"],
            "unique",
        ),
        ([], ["available", "available"], "unique declared"),
    ],
)
def test_lifecycle_declarations_are_consistent_graphs(transitions, initial, error):
    with pytest.raises(ValidationError, match=error):
        StoryLifecycleDeclaration.model_validate(
            {
                **case("reference plugin-update lifecycle"),
                "transitions": transitions,
                "initial_phases": initial,
            }
        )
    duplicate = case("reference plugin-update lifecycle")
    with pytest.raises(ValidationError, match="unique"):
        StoryLifecycleDeclaration.model_validate(
            {**duplicate, "phases": duplicate["phases"] + duplicate["phases"][:1]}
        )


def item(**changes):
    fields = dict(read_at=None, dismissed_at=None, deleted_at=None, expires_at=None, event_at=NOW)
    return SimpleNamespace(**{**fields, **changes})


ITEMS = {
    "unread": item(),
    "read": item(read_at=NOW),
    "dismissed": item(dismissed_at=NOW),
    "dismissed_read": item(read_at=NOW, dismissed_at=NOW),
    "deleted": item(deleted_at=NOW),
    "retention_expired": item(event_at=NOW - 40 * 86400),
    "transport_expired": item(expires_at=NOW),
}
CUTOFF = NOW - 30 * 86400


@pytest.mark.parametrize(
    "head_phase,item_name,phase,expected",
    [
        (None, None, "available", RevisionDecision.CREATE_STORY),
        (None, None, "installed", RevisionDecision.REJECT_TRANSITION),
        (None, None, "unknown", RevisionDecision.REJECT_TRANSITION),
        ("available", "unread", "installed", RevisionDecision.UPDATE_IN_PLACE),
        ("available", "unread", "available", RevisionDecision.UPDATE_IN_PLACE),
        ("available", "read", "installed", RevisionDecision.NEW_LINKED_ITEM),
        ("available", "dismissed", "failed", RevisionDecision.NEW_LINKED_ITEM),
        ("available", "dismissed_read", "resolved", RevisionDecision.NEW_LINKED_ITEM),
        ("available", "deleted", "installed", RevisionDecision.NEW_LINKED_ITEM),
        ("available", "retention_expired", "installed", RevisionDecision.NEW_LINKED_ITEM),
        ("available", "transport_expired", "installed", RevisionDecision.NEW_LINKED_ITEM),
        ("failed", "unread", "installed", RevisionDecision.UPDATE_IN_PLACE),
        ("failed", "read", "failed", RevisionDecision.NEW_LINKED_ITEM),
        ("failed", "unread", "available", RevisionDecision.REJECT_TRANSITION),
        ("installed", "unread", "available", RevisionDecision.REJECT_CLOSED),
        ("installed", "unread", "installed", RevisionDecision.REJECT_CLOSED),
        ("resolved", "read", "failed", RevisionDecision.REJECT_CLOSED),
        ("retired", "unread", "installed", RevisionDecision.REJECT_TRANSITION),
    ],
)
def test_story_revision_transition_table(head_phase, item_name, phase, expected):
    head = None if head_phase is None else StoryHead(head_phase, 3, ITEMS[item_name])
    decision = decide_revision(PLUGIN_UPDATES, head, phase, now=NOW, retention_cutoff=CUTOFF)
    assert decision == expected


def test_story_revision_matrix_invariants():
    """Exhaustively: in-place updates only for eligible items, terminal stories never change."""
    phases = [phase.id for phase in PLUGIN_UPDATES.phases]
    for head_phase, target, name in product(phases, phases, ITEMS):
        head = StoryHead(head_phase, 1, ITEMS[name])
        decision = decide_revision(PLUGIN_UPDATES, head, target, now=NOW, retention_cutoff=CUTOFF)
        if PLUGIN_UPDATES.phase(head_phase).terminal:
            assert decision == RevisionDecision.REJECT_CLOSED
        elif not PLUGIN_UPDATES.allows(head_phase, target):
            assert decision == RevisionDecision.REJECT_TRANSITION
        elif name == "unread":
            assert decision == RevisionDecision.UPDATE_IN_PLACE
        else:
            assert decision == RevisionDecision.NEW_LINKED_ITEM


def test_events_without_story_are_single_revision_items():
    lifecycle = single_item_lifecycle("plugin.notice")
    assert decide_revision(lifecycle, None, "posted", now=NOW, retention_cutoff=None) == (
        RevisionDecision.CREATE_STORY
    )
    head = StoryHead("posted", 1, ITEMS["unread"])
    assert decide_revision(lifecycle, head, "posted", now=NOW, retention_cutoff=None) == (
        RevisionDecision.REJECT_TRANSITION
    )
    assert {"recovery", "verification", "invitation", "test"}.isdisjoint(contract.STORY_PURPOSES)


def test_item_state_precedence_and_operations_table():
    assert item_state(ITEMS["unread"]) == ItemState.UNREAD
    assert item_state(ITEMS["read"]) == ItemState.READ
    assert item_state(ITEMS["dismissed_read"]) == ItemState.DISMISSED
    assert item_state(ITEMS["retention_expired"], retention_cutoff=CUTOFF) == ItemState.EXPIRED
    assert item_state(ITEMS["retention_expired"]) == ItemState.UNREAD
    assert item_state(item(deleted_at=NOW, dismissed_at=NOW)) == ItemState.DELETED
    assert all(
        ItemState.DELETED not in operation.sources for operation in INBOX_OPERATIONS.values()
    )
    assert INBOX_OPERATIONS["dismiss"].sources == {ItemState.UNREAD, ItemState.READ}


def test_lifecycle_replay_vocabulary_matches_contract_tables():
    """Replay statuses are exactly item operations, delivery states and the 1.1.14 revision."""
    replay = set(get_args(NotificationLifecycleEvent.model_fields["status"].annotation))
    items = {operation.lifecycle_status for operation in INBOX_OPERATIONS.values()}
    # "revised" is the Plugin API 1.1.14 addition, replayed only on request.
    assert replay == {"created", "revised"} | items | set(contract.DELIVERY_TRANSITIONS)
    for status, targets in contract.DELIVERY_TRANSITIONS.items():
        assert targets.issubset(contract.DELIVERY_TRANSITIONS), status
    assert contract.DELIVERY_INITIAL_STATUSES <= set(contract.DELIVERY_TRANSITIONS)


def capabilities(**changes):
    return NotificationProviderCapabilities.model_validate(
        {**case("rich provider capabilities"), **changes}
    )


@pytest.mark.parametrize(
    "caps,delivered,current,linked,operation",
    [
        ({}, False, False, False, "send"),
        ({}, True, True, False, "edit"),
        ({"message_updates": "replace"}, True, True, False, "edit"),
        ({"message_updates": "none"}, True, True, False, "follow_up"),
        ({}, True, False, False, "follow_up"),
        ({}, True, True, True, "follow_up"),
    ],
)
def test_reconciliation_uses_explicit_capability_fallbacks(
    caps, delivered, current, linked, operation
):
    assert (
        reconciliation_operation(
            capabilities(**caps),
            previously_delivered=delivered,
            remote_reference_current=current,
            new_linked_item=linked,
        )
        == operation
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"text_formats": ["plain", "plain"]},
        {"links": False, "live_page_links": False, "action_buttons": 2},
        {"images": {"max_images": 2}},
        {"images": {"max_images": 0, "external_urls": True}},
        {"max_batch_items": 0},
        {"max_batch_items": 51},
        {"message_updates": "delete"},
        {"secure": True},
        {"recovery_supported": True},
        {"sensitive_supported": True},
        {"critical_supported": True},
        {"max_batch_items": True},
    ],
)
def test_provider_capabilities_reject_invalid_or_authority_claims(changes):
    with pytest.raises(ValidationError):
        capabilities(**changes)


def render(**changes):
    return RendererContribution.model_validate({**case("email HTML contribution"), **changes})


def test_renderer_contributions_accept_declared_placeholders_only():
    template = "{title} {summary} {blocks} {facts} {actions} {attribution} {occurred_at}"
    accepted = render(template=f"<p>{template} {{expires_at}} {{live_link}} {{fact_Count_2}}</p>")
    assert accepted.surface == "email"
    for template in ("{0}", "{}", "{facts[0]}", "{title!s}", "{fact_}", "{ title }", "{title", "}"):
        with pytest.raises(ValidationError):
            render(template=template)
    for changes in (
        {"kind": "fields", "template": None},
        {"kind": "fields", "fields": ["title", "title"], "template": None},
        {"kind": "fields", "fields": ["recipient"], "template": None},
        {"kind": "markdown_template", "fields": ["title"]},
        {"template": None},
        {"surface": "sms"},
        {"template": "<p>{title}\x07</p>"},
    ):
        with pytest.raises(ValidationError):
            render(**changes)


@pytest.mark.parametrize(
    "markup",
    [
        "<SCRIPT src=https://x.example/a.js></SCRIPT>",
        "< script>alert(1)</script>",
        '<a href="JaVaScRiPt:alert(1)">x</a>',
        "<a href= 'vbscript:x'>x</a>",
        '<img src="data:image/svg+xml;base64,AA">',
        '<div style="width: expression(alert(1))">x</div>',
        "<style>@import url(https://x.example/a.css)</style>",
        "<svg><circle/></svg>",
        "<object data=x></object>",
        "<embed src=x>",
        "<form action=https://x.example><input name=a></form>",
        "<base href=https://x.example/>",
        '<meta http-equiv="refresh" content="0;url=https://x.example">',
        '<link rel="stylesheet" href="https://x.example/a.css">',
        '<body onload="x()">',
        "<img/onerror=alert(1) src=x>",
    ],
)
def test_html_templates_reject_executable_or_embedded_markup(markup):
    with pytest.raises(ValidationError):
        render(template=markup)


def test_public_contract_module_needs_no_host_internals():
    """No provider or plugin needs a private host-only import to adopt the contract."""
    source = Path(contract.__file__).parents[1] / "plugin_api" / "notification_story_contracts.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert imported == {
        "ipaddress",
        "re",
        "string",
        "typing",
        "urllib.parse",
        "uuid",
        "pydantic",
        ".base_contracts",
    }
    for name in (
        "NOTIFICATION_CONTRACT_VERSION",
        "CanonicalNotificationEvent",
        "NotificationContent",
        "NotificationProviderCapabilities",
        "RendererContribution",
        "StoryLifecycleDeclaration",
        "negotiate_notification_contract",
        "notification_contract_reason",
        "notification_contract_schemas",
    ):
        assert name in public_api.__all__ and getattr(public_api, name) is not None
