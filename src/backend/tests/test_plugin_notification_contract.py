"""Plugin API 1.1.14: versioned canonical notification contract for plugins.

Covers the registration schema, event conformance, host-owned authority, grants rechecked at
registration/publication/dispatch/action time, negotiation and retention after disablement.
"""

import time
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update

from src.database.models.notification import Notification
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.notification_story import NotificationStory, NotificationStoryRevision
from src.database.models.plugin_notification_type import PluginNotificationTypeRegistration
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import notification_story_pages as pages
from src.features.notification_source_policy import retire_sources, source_delivery_allowed
from src.plugin_api import (
    CanonicalNotificationEvent,
    CanonicalNotificationTypeRegistration,
    check_event_against_registration,
)
from src.plugin_api.gateway import dispatch_gateway_request
from src.plugin_api.notification_story_contracts import NOTIFICATION_CONTRACT_VERSION

GRANTS = (
    "notification_sources.register",
    "notifications.canonical",
    "notifications.actions",
    "notifications.urgency",
    "notifications.templates.html",
)


def registration(plugin, **changes):
    event_type = f"{plugin}.update"
    document = {
        "contract_version": NOTIFICATION_CONTRACT_VERSION,
        "event_type": event_type,
        "label": "Update",
        "description": "A plugin update lifecycle.",
        "max_urgency": "critical",
        "story_key_prefix": "update:",
        "grouping": {"strategy": "key", "merge": "digest"},
        "lifecycle": {
            "contract_version": NOTIFICATION_CONTRACT_VERSION,
            "event_type": event_type,
            "phases": [
                {"id": "available", "label": "Available"},
                {"id": "installed", "label": "Installed", "terminal": True},
                {"id": "failed", "label": "Failed", "terminal": True, "severity": "error"},
            ],
            "initial_phases": ["available"],
            "transitions": [
                {"from_phase": "available", "to_phase": "available"},
                {"from_phase": "available", "to_phase": "installed"},
                {"from_phase": "available", "to_phase": "failed"},
            ],
        },
        "facts": [
            {"key": "version", "label": "Version", "kind": "text"},
            {"key": "size", "label": "Size", "kind": "integer", "required": False},
        ],
        "actions": [
            {
                "action_id": "install.apply",
                "label": "Install",
                "input": {"channel": {"type": "string", "max_length": 20}},
            }
        ],
        "renderers": [
            {
                "contract_version": NOTIFICATION_CONTRACT_VERSION,
                "event_type": event_type,
                "surface": "chat",
                "kind": "markdown_template",
                "template": "**{title}** {fact_version}",
            }
        ],
    }
    document.update(changes)
    return document


def event(plugin, phase="available", **changes):
    document = {
        "contract_version": NOTIFICATION_CONTRACT_VERSION,
        "event_type": f"{plugin}.update",
        "event_id": f"evt-{uuid4().hex[:12]}",
        "occurred_at": int(time.time()),
        "story": {"key": "update:2.0", "phase": phase},
        "grouping_key": "updates",
        "content": {
            "title": f"Update {phase}",
            "summary": "Version 2.0 is ready.",
            "facts": [
                {"key": "version", "label": "Version", "value": {"kind": "text", "value": "2.0"}}
            ],
            "actions": [
                {
                    "id": "install",
                    "label": "Install",
                    "target": {
                        "kind": "plugin_action",
                        "action_id": "install.apply",
                        "input": {"channel": "stable"},
                    },
                }
            ],
        },
    }
    document.update(changes)
    return document


def with_content(plugin, **changes):
    base = event(plugin)
    base["content"] = {**base["content"], **changes}
    return base


class Source:
    def __init__(self, owner, plugin, installation):
        self.owner, self.plugin, self.installation = owner, plugin, installation

    async def call(self, method, payload, capability=None):
        capability = capability or {
            "notification_sources.register": "notification_sources.register",
            "notification_sources.unregister": "notification_sources.register",
        }.get(method, "notifications.canonical")
        async with SessionLocal() as db:
            return await dispatch_gateway_request(
                db,
                plugin_id=self.plugin,
                installation_id=self.installation,
                user_id=self.owner,
                method=method,
                capability=capability,
                payload=payload,
            )

    async def register(self, **changes):
        return await self.call(
            "notification_sources.register", registration(self.plugin, **changes)
        )

    async def publish(self, payload):
        return await self.call("notifications.publish", payload)

    async def revoke(self, *capabilities):
        async with SessionLocal() as db:
            await db.execute(
                update(PluginPermissionGrant)
                .where(
                    PluginPermissionGrant.plugin_id == self.plugin,
                    PluginPermissionGrant.capability.in_(capabilities),
                )
                .values(revoked_at=int(time.time()))
            )
            await db.commit()

    async def notifications(self):
        async with SessionLocal() as db:
            return list(
                await db.scalars(
                    select(Notification)
                    .where(Notification.user_id == self.owner)
                    .order_by(Notification.event_at, Notification.created_at)
                )
            )

    async def receipts(self):
        async with SessionLocal() as db:
            return await db.scalar(
                select(func.count())
                .select_from(NotificationReceipt)
                .where(NotificationReceipt.user_id == self.owner)
            )


@pytest.fixture(name="source")
async def source_fixture():
    owner, installation = uuid4(), uuid4()
    plugin = f"example.canon-{uuid4().hex[:10]}"
    async with SessionLocal() as db:
        db.add(
            User(
                id=owner, username=owner.hex, email=f"{owner}@example.test", password_hash="unused"
            )
        )
        await db.flush()
        for capability in GRANTS:
            db.add(
                PluginPermissionGrant(
                    plugin_id=plugin,
                    installation_id=installation,
                    user_id=owner,
                    capability=capability,
                    capability_version=1,
                )
            )
        await db.commit()
    try:
        yield Source(owner, plugin, installation)
    finally:
        async with SessionLocal() as db:
            await db.execute(
                delete(PluginNotificationTypeRegistration).where(
                    PluginNotificationTypeRegistration.plugin_id == plugin
                )
            )
            await db.execute(
                delete(PluginPermissionGrant).where(PluginPermissionGrant.plugin_id == plugin)
            )
            await db.execute(delete(User).where(User.id == owner))
            await db.commit()


# --- registration schema --------------------------------------------------------------------


def valid(**changes):
    return CanonicalNotificationTypeRegistration.model_validate(
        registration("example.test", **changes)
    )


def test_registration_accepts_a_complete_declaration():
    declaration = valid()
    assert declaration.action("install.apply") is not None
    assert declaration.default_category == "plugin" and not declaration.needs_html_grant


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": str(uuid4())},
        {"recipient": "someone"},
        {"destination_id": str(uuid4())},
        {"credentials": {"token": "x"}},
        {"default_category": "system"},
        {"purpose": "recovery"},
        {"purpose": "security"},
        {"contract_version": "2.0.0"},
        {"contract_version": "1.9.0"},
        {"contract_version": "latest"},
        {"description": "x" * 501},
        {"label": ""},
        {"label": "multi\nline"},
        {"facts": [{"key": "a", "label": "A", "kind": "text"}] * 2},
        {"facts": [{"key": f"k{i}", "label": "A", "kind": "text"} for i in range(25)]},
        {"facts": [{"key": "a", "label": "A", "kind": "float"}]},
        {"actions": [{"action_id": "Bad Id", "label": "A"}]},
        {"actions": [{"action_id": "a.b", "label": "A", "requires": ["api.full"]}]},
        {"actions": [{"action_id": "a.b", "label": "A", "requires": ["notifications.sensitive"]}]},
        {"actions": [{"action_id": "a.b", "label": "A", "requires": ["nonsense.capability"]}]},
        {"actions": [{"action_id": "a.b", "label": "A", "input": {"x": {"type": "object"}}}]},
        {"actions": [{"action_id": "a.b", "label": "A", "script": "alert(1)"}]},
        {"story_key_prefix": ""},
        {"grouping": {"strategy": "everything"}},
    ],
)
def test_registration_rejects_unsafe_oversized_or_unsupported_declarations(changes):
    with pytest.raises(ValidationError):
        valid(**changes)


def test_registration_ties_lifecycle_and_contributions_to_its_own_event_type():
    declaration = registration("example.test")
    with pytest.raises(ValidationError, match="another event type"):
        CanonicalNotificationTypeRegistration.model_validate(
            {
                **declaration,
                "lifecycle": {**declaration["lifecycle"], "event_type": "example.test.x"},
            }
        )
    other = {**declaration["renderers"][0], "event_type": "example.test.other"}
    with pytest.raises(ValidationError, match="this event type"):
        CanonicalNotificationTypeRegistration.model_validate({**declaration, "renderers": [other]})
    with pytest.raises(ValidationError, match="declared facts"):
        valid(
            renderers=[
                {**declaration["renderers"][0], "template": "{fact_undeclared}"},
            ]
        )
    with pytest.raises(ValidationError, match="one renderer"):
        valid(renderers=[declaration["renderers"][0]] * 2)


@pytest.mark.parametrize(
    "template",
    [
        "<script>alert(1)</script>",
        '<a href="javascript:alert(1)">x</a>',
        "<img src=x onerror=alert(1)>",
        "<iframe src=//evil></iframe>",
        "{title.__class__}",
        "{title!r}",
        "{title:>1000000}",
    ],
)
def test_html_templates_cannot_carry_executable_content(template):
    declaration = registration("example.test")
    contribution = {
        **declaration["renderers"][0],
        "surface": "email",
        "kind": "html_template",
        "template": template,
    }
    with pytest.raises(ValidationError):
        valid(renderers=[contribution])


def test_security_types_need_secure_destinations_and_html_needs_its_grant_flag():
    with pytest.raises(ValidationError, match="SECURE"):
        valid(purpose="security", required_trust="PRIVATE")
    assert valid(purpose="security", required_trust="SECURE").required_trust == "SECURE"
    declaration = registration("example.test")
    html = {
        **declaration["renderers"][0],
        "surface": "email",
        "kind": "html_template",
        "template": "<p>{title}</p>",
    }
    assert valid(renderers=[html]).needs_html_grant


# --- event conformance ----------------------------------------------------------------------


def conforms(**changes):
    plugin = "example.test"
    declaration = valid()
    document = event(plugin)
    for key, value in changes.items():
        document[key] = value
    check_event_against_registration(
        declaration, CanonicalNotificationEvent.model_validate(document)
    )


def test_conforming_event_passes():
    conforms()


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"event_type": "example.test.other"}, "another event type"),
        ({"story": None}, "belong to a story"),
        ({"story": {"key": "other:1", "phase": "available"}}, "prefix"),
        ({"story": {"key": "update:1", "phase": "unknown"}}, "phase"),
        ({"urgency": "critical"}, None),
        ({"grouping_key": None}, "grouping key"),
    ],
)
def test_events_must_follow_the_declaration(changes, message):
    plugin = "example.test"
    declaration = valid(max_urgency="normal")
    document = {**event(plugin), **changes}
    parsed = CanonicalNotificationEvent.model_validate(document)
    with pytest.raises(ValueError, match=message or "urgency"):
        check_event_against_registration(declaration, parsed)


def test_facts_must_match_their_declared_schema():
    plugin = "example.test"
    declaration = valid()

    def check(**content):
        document = event(plugin)
        document["content"] = {**document["content"], **content}
        check_event_against_registration(
            declaration, CanonicalNotificationEvent.model_validate(document)
        )

    version = {"key": "version", "label": "Version", "value": {"kind": "text", "value": "2"}}
    check(
        facts=[version, {"key": "size", "label": "Size", "value": {"kind": "integer", "value": 3}}]
    )
    with pytest.raises(ValueError, match="not declared"):
        check(
            facts=[version, {"key": "extra", "label": "E", "value": {"kind": "text", "value": "x"}}]
        )
    with pytest.raises(ValueError, match="Required fact"):
        check(facts=[])
    with pytest.raises(ValueError, match="kind integer"):
        check(
            facts=[version, {"key": "size", "label": "S", "value": {"kind": "text", "value": "3"}}]
        )


@pytest.mark.parametrize(
    "target,message",
    [
        ({"action_id": "wipe.all", "input": {}}, "not declared"),
        ({"action_id": "install.apply", "input": {}}, "required"),
        ({"action_id": "install.apply", "input": {"channel": 5}}, "type string"),
        ({"action_id": "install.apply", "input": {"channel": "x" * 21}}, "declared length"),
        ({"action_id": "install.apply", "input": {"channel": "a", "extra": "b"}}, "not declared"),
    ],
)
def test_action_targets_must_match_declared_typed_inputs(target, message):
    document = event("example.test")
    document["content"]["actions"][0]["target"] = {"kind": "plugin_action", **target}
    parsed = CanonicalNotificationEvent.model_validate(document)
    with pytest.raises(ValueError, match=message):
        check_event_against_registration(valid(), parsed)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["content"].update(title="x" * 501),
        lambda d: d["content"].update(summary="x" * 10_001),
        lambda d: d["content"].update(
            facts=[
                {"key": f"k{i}", "label": "L", "value": {"kind": "text", "value": "v"}}
                for i in range(25)
            ]
        ),
        lambda d: d["content"]["actions"][0]["target"].update(action_id="Bad Id"),
        lambda d: d["content"]["actions"].extend([deepcopy(d["content"]["actions"][0])] * 5),
        lambda d: d["content"].update(
            actions=[
                {
                    "id": "go",
                    "label": "Go",
                    "target": {"kind": "link", "url": "http://insecure.example.com"},
                }
            ]
        ),
        lambda d: d["content"].update(
            actions=[
                {
                    "id": "go",
                    "label": "Go",
                    "target": {"kind": "link", "url": "javascript:alert(1)"},
                }
            ]
        ),
        lambda d: d.update(recipient="x"),
        lambda d: d.update(destination_id=str(uuid4())),
        lambda d: d.update(contract_version="2.0.0"),
        lambda d: d.update(urgency="emergency"),
    ],
)
def test_event_schema_rejects_oversized_or_unsafe_documents(mutation):
    document = event("example.test")
    mutation(document)
    with pytest.raises(ValidationError):
        CanonicalNotificationEvent.model_validate(document)


# --- gateway: registration, publication, authority -------------------------------------------


async def test_register_and_publish_create_a_host_owned_story(source):
    assert (await source.register())["registered"] is True
    result = await source.publish(event(source.plugin))
    assert result["created"] is True
    (notice,) = await source.notifications()
    # The host chose recipient, source and installation, trust and category.
    assert notice.user_id == source.owner and notice.source == source.plugin
    assert notice.source_installation_id == source.installation
    assert notice.required_trust == 1 and notice.purpose == "standard"
    assert notice.group_key == f"{source.plugin}.update:updates"
    assert notice.story_id is not None and notice.story_revision == 1
    async with SessionLocal() as db:
        revision = await db.scalar(
            select(NotificationStoryRevision).where(
                NotificationStoryRevision.story_id == notice.story_id
            )
        )
        assert revision.content["facts"][0]["key"] == "version"
        assert await db.scalar(
            select(NotificationStory.scope_key).where(NotificationStory.id == notice.story_id)
        ) == str(source.installation)


async def test_separate_merge_requests_no_digest_grouping(source):
    declaration = registration(source.plugin)
    await source.register(grouping={**declaration["grouping"], "merge": "separate"})
    await source.publish(event(source.plugin))
    (notice,) = await source.notifications()
    assert notice.group_key is None


async def test_story_lifecycle_advances_and_rejects_undeclared_transitions(source):
    await source.register()
    await source.publish(event(source.plugin, "available"))
    await source.publish(event(source.plugin, "installed"))
    with pytest.raises(ValueError, match="transition"):
        await source.publish(event(source.plugin, "available"))
    notices = await source.notifications()
    # The unread head item evolved in place to revision 2; the rejected event changed nothing.
    assert [n.story_revision for n in notices] == [2]


async def test_identical_replays_are_idempotent_and_changed_replays_conflict(source):
    await source.register()
    document = event(source.plugin)
    assert (await source.publish(document))["created"] is True
    assert (await source.publish(document))["created"] is False
    assert len(await source.notifications()) == 1
    changed = deepcopy(document)
    changed["content"]["summary"] = "Different"
    with pytest.raises(ValueError):
        await source.publish(changed)
    assert len(await source.notifications()) == 1


async def test_unregistered_or_legacy_types_cannot_publish(source):
    with pytest.raises(PermissionError, match="not active"):
        await source.publish(event(source.plugin))
    await source.call(
        "notification_sources.register",
        {
            "event_type": f"{source.plugin}.update",
            "label": "Legacy",
            "title_template": "T",
            "body_template": "B",
        },
    )
    with pytest.raises(PermissionError, match="not active"):
        await source.publish(event(source.plugin))


async def test_registration_requires_each_capability_it_uses(source):
    await source.revoke("notifications.canonical")
    with pytest.raises(PermissionError, match="notifications.canonical"):
        await source.register()
    async with SessionLocal() as db:
        await db.execute(
            delete(PluginPermissionGrant).where(PluginPermissionGrant.plugin_id == source.plugin)
        )
        for capability in ("notification_sources.register", "notifications.canonical"):
            db.add(
                PluginPermissionGrant(
                    plugin_id=source.plugin,
                    installation_id=source.installation,
                    user_id=source.owner,
                    capability=capability,
                    capability_version=1,
                )
            )
        await db.commit()
    with pytest.raises(PermissionError, match="notifications.urgency"):
        await source.register()
    with pytest.raises(PermissionError, match="notifications.actions"):
        await source.register(max_urgency="normal")
    declaration = registration(source.plugin, max_urgency="normal", actions=[])
    with pytest.raises(PermissionError, match="notifications.templates.html"):
        await source.register(
            max_urgency="normal",
            actions=[],
            renderers=[
                {
                    **declaration["renderers"][0],
                    "surface": "email",
                    "kind": "html_template",
                    "template": "<p>{title}</p>",
                }
            ],
        )
    assert (await source.register(max_urgency="normal", actions=[]))["registered"] is True


async def test_registration_must_use_the_plugins_own_namespace(source):
    for event_type in ("someone.else.update", "system.update", "auth.update"):
        bad = registration(source.plugin)
        bad["event_type"] = event_type
        bad["lifecycle"]["event_type"] = event_type
        bad["renderers"] = []
        with pytest.raises(ValueError, match="namespace"):
            await source.call("notification_sources.register", bad)


async def test_urgency_and_actions_need_their_own_grants_at_publication(source):
    await source.register()
    urgent = event(source.plugin, urgency="critical")
    await source.revoke("notifications.urgency")
    with pytest.raises(PermissionError, match="notifications.urgency"):
        await source.publish(urgent)
    await source.revoke("notifications.actions")
    with pytest.raises(PermissionError, match="notifications.actions"):
        await source.publish(event(source.plugin))
    assert await source.receipts() == 0


async def test_critical_urgency_is_only_a_request_stored_for_host_policy(source):
    await source.register()
    await source.publish(event(source.plugin, urgency="critical"))
    (notice,) = await source.notifications()
    assert notice.requested_urgency == "critical"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["content"].update(summary="Visit <script>alert(1)</script>"),
        lambda d: d["content"].update(summary="token=abcdefghijklmnop1234567890"),
        lambda d: d["content"].update(
            blocks=[{"type": "markdown", "markdown": "<iframe src=//e></iframe>"}]
        ),
        lambda d: d["content"].update(
            actions=[
                {
                    "id": "go",
                    "label": "Go",
                    "target": {"kind": "link", "url": "https://example.com/p?token=secret"},
                }
            ]
        ),
        lambda d: d["content"].update(
            images=[{"source": {"kind": "external", "url": "https://localhost.local/a.png"}}]
        ),
    ],
)
async def test_unsafe_content_is_rejected_without_consuming_a_receipt(source, mutation):
    await source.register()
    document = event(source.plugin)
    document["content"]["actions"] = []
    mutation(document)
    with pytest.raises(ValueError):
        await source.publish(document)
    assert await source.receipts() == 0 and await source.notifications() == []


async def test_markdown_links_with_script_schemes_render_inert(source):
    from src.features.notification_rich import render_email_html, render_plain
    from src.plugin_api.notification_story_contracts import NotificationContent

    await source.register()
    document = event(source.plugin)
    document["content"]["summary"] = "[x](javascript:alert(1)) and ![i](data:text/html,x)"
    await source.publish(document)
    content = NotificationContent.model_validate(document["content"])
    for rendered in (render_email_html(content), render_plain(content)):
        assert "javascript:" not in rendered.replace("(javascript:alert(1))", "")
        assert "<script" not in rendered and 'href="javascript' not in rendered


async def test_invalid_documents_report_a_bounded_reason_without_echoing_input(source):
    await source.register()
    document = event(source.plugin)
    document["content"]["summary"] = "x" * 10_001
    with pytest.raises(ValueError) as caught:
        await source.publish(document)
    assert "content.summary" in str(caught.value) and "xxxxxxxx" not in str(caught.value)


async def test_event_in_the_future_or_ancient_past_is_rejected(source):
    await source.register()
    for offset in (3600, -40 * 86400):
        with pytest.raises(ValueError, match="interval"):
            await source.publish(event(source.plugin, occurred_at=int(time.time()) + offset))


# --- revocation, disablement and retention ---------------------------------------------------


async def test_revoked_grants_stop_queued_source_work(source):
    await source.register()
    await source.publish(event(source.plugin, urgency="critical"))
    (notice,) = await source.notifications()
    async with SessionLocal() as db:
        assert await source_delivery_allowed(db, notice)
    await source.revoke("notifications.urgency")
    async with SessionLocal() as db:
        assert not await source_delivery_allowed(db, notice)
    async with SessionLocal() as db:
        await db.execute(
            update(PluginPermissionGrant)
            .where(PluginPermissionGrant.plugin_id == source.plugin)
            .values(revoked_at=None)
        )
        await db.commit()
    await source.revoke("notifications.canonical")
    async with SessionLocal() as db:
        assert not await source_delivery_allowed(db, notice)


async def test_disable_or_uninstall_suppresses_work_but_keeps_history(source):
    await source.register()
    await source.publish(event(source.plugin))
    (notice,) = await source.notifications()
    async with SessionLocal() as db:
        await retire_sources(db, source.plugin)
        await db.commit()
    async with SessionLocal() as db:
        assert not await source_delivery_allowed(db, notice)
    with pytest.raises(PermissionError, match="not active"):
        await source.publish(event(source.plugin, "installed", event_id="evt-after"))
    # Retained history: the item, its story, its revision and its receipt survive.
    assert len(await source.notifications()) == 1
    assert await source.receipts() == 1
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(NotificationStoryRevision)) >= 1


async def test_reregistration_after_disable_does_not_release_old_work(source):
    await source.register()
    await source.publish(event(source.plugin))
    (notice,) = await source.notifications()
    async with SessionLocal() as db:
        await retire_sources(db, source.plugin)
        await db.commit()
    await source.register()
    async with SessionLocal() as db:
        assert await source_delivery_allowed(db, notice)  # the type is active again
        assert (
            await db.scalar(
                select(Notification.source_installation_id).where(Notification.id == notice.id)
            )
            == source.installation
        )


async def test_lifecycle_replay_exposes_no_private_facts_publicly(source):
    await source.register()
    document = event(source.plugin)
    document["content"]["facts"][0]["value"]["value"] = "internal-build-7731"
    await source.publish(document)
    await source.publish(document)  # replay
    async with SessionLocal() as db:
        story = await db.scalar(
            select(NotificationStory).where(NotificationStory.user_id == source.owner)
        )
        state = await pages.owned_state(db, source.owner, story.id)
        assert "internal-build-7731" in str(state.revision.content)
        assert "internal-build-7731" not in str(pages.public_view(state))


# --- negotiation ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "declared,negotiated",
    [("1.0.0", "1.0.0"), ("1.7.2", NOTIFICATION_CONTRACT_VERSION), ("2.0.0", None), ("beta", None)],
)
async def test_version_negotiation(source, declared, negotiated):
    result = await source.call("notifications.contract", {"contract_version": declared})
    assert result["negotiated"] == negotiated
    assert result["host_contract_version"] == NOTIFICATION_CONTRACT_VERSION
    assert result["plugin_api_version"] == "1.1.14" and "typed_actions" in result["features"]
    assert (result["reason"] is None) == (negotiated is not None)


async def test_negotiation_needs_the_canonical_grant(source):
    await source.revoke("notifications.canonical")
    with pytest.raises(PermissionError):
        await source.call("notifications.contract", {"contract_version": "1.0.0"})
    with pytest.raises(PermissionError):
        await source.call(
            "notifications.contract", {"contract_version": "1.0.0"}, "notifications.send"
        )


def test_unknown_gateway_method_and_old_capabilities_do_not_reach_canonical_publish():
    from src.plugin_api.capabilities import capability_implies

    assert not capability_implies("notifications.emit", "notifications.canonical")
    assert not capability_implies("notifications", "notifications.canonical")
    for name in ("canonical", "actions", "urgency", "templates.html"):
        assert not capability_implies("api.full", f"notifications.{name}")
    assert not capability_implies("notifications", "notifications.templates.html")
    assert not capability_implies("notifications.canonical", "notifications.urgency")


# --- lifecycle replay of in-place revisions ---------------------------------------------------


async def replay(source, **query):
    from src.features.notification_audit import publish_lifecycle

    async with SessionLocal() as db:
        await publish_lifecycle(db)
        await db.commit()
    return await source.call("notifications.lifecycle.poll", query, "notifications.lifecycle.read")


async def test_in_place_revisions_replay_a_versioned_status_without_content(source):
    async with SessionLocal() as db:
        db.add(
            PluginPermissionGrant(
                plugin_id=source.plugin,
                installation_id=source.installation,
                user_id=source.owner,
                capability="notifications.lifecycle.read",
                capability_version=1,
            )
        )
        await db.commit()
    await source.register()
    first = event(source.plugin, "available")
    first["content"]["facts"][0]["value"]["value"] = "private-build-4412"
    await source.publish(first)
    await source.publish(event(source.plugin, "installed"))  # unread head: revised in place
    older = await replay(source)  # a 1.1.4 consumer
    assert [item["status"] for item in older["events"]] == ["created"]
    newer = await replay(source, include_revisions=True)
    assert [item["status"] for item in newer["events"]] == ["created", "revised"]
    assert "private-build-4412" not in str(newer) and "Version" not in str(newer)
    assert set(newer["events"][1]) == {
        "id",
        "notification_id",
        "delivery_id",
        "status",
        "occurred_at",
    }
