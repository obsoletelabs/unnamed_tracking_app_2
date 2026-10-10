"""Namespaced source contracts, grants, routing and durable lifecycle regressions."""

import time
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select

from src.core.preferences import save_preferences
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.plugin_notification_type import PluginNotificationTypeRegistration
from src.database.models.plugin_permissions import PluginPermissionGrant
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notification_enrollment import create_email, update_email
from src.features.notification_policy import INBOX_PROVIDER, Trust, select_projection
from src.features.notification_providers.delivery import (
    _claim,
    _dispatch,
    process_pending_deliveries,
)
from src.features.notification_settings import routing_settings
from src.features.notification_source_policy import retire_sources, source_delivery_allowed
from src.plugin_api.capabilities import capability_implies
from src.plugin_api.gateway import dispatch_gateway_request
from src.plugin_api.notification_contracts import (
    NotificationEventEmission,
    NotificationTypeRegistration,
)
from tests.test_notification_core import endpoint
from tests.test_notification_email import mailbox as mailbox
from tests.test_notification_email import verify


def definition(plugin, **changes):
    return {
        "event_type": f"{plugin}.release",
        "label": "Plugin release",
        "description": "A registered release observation.",
        "title_template": "Release: {title}",
        "body_template": "Released {count} items.",
        "parameters": {"title": "string", "count": "integer"},
        **changes,
    }


def event(plugin, **changes):
    return {
        "event_type": f"{plugin}.release",
        "dedupe_key": "release-1",
        "occurred_at": int(time.time()),
        "data": {"title": "Example", "count": 3},
        "group_key": "releases",
        **changes,
    }


async def call(db, source, method, payload):
    owner, plugin, installation = source
    capability = (
        "notifications.emit" if method == "notifications.emit" else "notification_sources.register"
    )
    return await dispatch_gateway_request(
        db,
        plugin_id=plugin,
        installation_id=installation,
        user_id=owner,
        method=method,
        capability=capability,
        payload=payload,
    )


@pytest.fixture
async def source():
    owner, installation = uuid4(), uuid4()
    plugin = f"example.source-{uuid4().hex}"
    async with SessionLocal() as db:
        db.add(
            User(
                id=owner, username=owner.hex, email=f"{owner}@example.test", password_hash="unused"
            )
        )
        await db.flush()
        for capability in ("notification_sources.register", "notifications.emit"):
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
        yield owner, plugin, installation
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


@pytest.mark.parametrize(
    "changes",
    [
        {"required_trust": "PUBLIC"},
        {"purpose": "recovery"},
        {"title_template": "{title.__class__}"},
        {"body_template": "{count:1000000000}"},
        {"title_template": "{title!r}"},
        {"body_template": "{undeclared}"},
        {"parameters": {"bad.name": "string"}},
        {"user_id": str(uuid4())},
        {"destination_id": str(uuid4())},
    ],
)
def test_source_declaration_rejects_unsafe_or_unowned_policy(changes):
    with pytest.raises(ValidationError):
        NotificationTypeRegistration.model_validate(definition("example.test", **changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": str(uuid4())},
        {"required_trust": "PUBLIC"},
        {"data": {"title": {"nested": "text"}}},
        {"data": {"title": "a" * 1001}},
        {"data": {"count": 2**54}},
    ],
)
def test_event_rejects_recipient_policy_and_unbounded_values(changes):
    with pytest.raises(ValidationError):
        NotificationEventEmission.model_validate(event("example.test", **changes))


def test_sensitive_grant_cannot_be_inherited():
    for parent in ("notifications", "api.full", "notifications.emit"):
        assert not capability_implies(parent, "notifications.sensitive")
    assert capability_implies("notifications.sensitive", "notifications.sensitive")


async def test_source_is_scoped_deduplicated_and_host_interpreted(source):
    owner, plugin, installation = source
    async with SessionLocal() as db:
        for forged in ("auth.password_reset", "other.plugin.release"):
            with pytest.raises(ValueError, match="namespace"):
                await call(
                    db,
                    source,
                    "notification_sources.register",
                    definition(plugin, event_type=forged),
                )
        await call(db, source, "notification_sources.register", definition(plugin))
        facts = event(plugin)
        result = await call(db, source, "notifications.emit", facts)
        notice = await db.get(Notification, UUID(result["notification_id"]))
        assert notice.user_id == owner and notice.source_installation_id == installation
        assert notice.event_type == f"{plugin}.release" and notice.title == "Release: Example"
        assert notice.body == "Released 3 items." and notice.group_key.endswith(":releases")
        assert notice.required_trust == Trust.PRIVATE and notice.inbox_visible
        receipt = await db.scalar(
            select(NotificationReceipt).where(NotificationReceipt.user_id == owner)
        )
        assert abs(receipt.accepted_at - int(time.time())) <= 2
        assert not (await call(db, source, "notifications.emit", facts))["created"]
        with pytest.raises(ValueError, match="conflicting facts"):
            await call(
                db,
                source,
                "notifications.emit",
                {**facts, "data": {"title": "Changed", "count": 3}},
            )
        await db.rollback()
        with pytest.raises(ValidationError):
            await call(db, source, "notifications.emit", {**facts, "user_id": str(uuid4())})


async def test_sensitive_source_requires_exact_grant_and_secure_external_recovery(source):
    owner, plugin, installation = source
    declaration = definition(plugin, required_trust="SECURE", purpose="recovery")
    async with SessionLocal() as db:
        db.add(
            PluginPermissionGrant(
                plugin_id=plugin,
                installation_id=installation,
                capability="api.full",
                capability_version=1,
                user_id=owner,
            )
        )
        await db.commit()
        with pytest.raises(PermissionError, match="high-risk"):
            await call(db, source, "notification_sources.register", declaration)
        db.add(
            PluginPermissionGrant(
                plugin_id=plugin,
                installation_id=installation,
                capability="notifications.sensitive",
                capability_version=1,
                user_id=owner,
            )
        )
        await db.commit()
        await call(db, source, "notification_sources.register", declaration)
        result = await call(db, source, "notifications.emit", event(plugin))
        notice = await db.get(Notification, UUID(result["notification_id"]))
        assert not notice.inbox_visible
        assert not list(
            await db.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == notice.id
                )
            )
        )
        for trust in (Trust.PUBLIC, Trust.PRIVATE, Trust.SECURE):
            target = endpoint(owner, trust, recovery_allowed=True)
            assert bool(select_projection(notice, target, {})) == (trust == Trust.SECURE)
        assert (
            select_projection(notice, endpoint(owner, Trust.SECURE, recovery_allowed=False), {})
            is None
        )
        inbox = endpoint(
            owner,
            Trust.SECURE,
            provider_id=INBOX_PROVIDER,
            channel_context="internal",
            recovery_allowed=True,
        )
        assert select_projection(notice, inbox, {}) is None
        assert notice.expires_at == notice.event_at + 600


async def test_type_preferences_are_independent_of_legacy_plugin_switch(source):
    owner, plugin, _ = source
    async with SessionLocal() as db:
        await call(db, source, "notification_sources.register", definition(plugin))
        await save_preferences(db, owner, {"notification_types": {"plugin.notice": False}})
        assert (await call(db, source, "notifications.emit", event(plugin)))["created"]
        await save_preferences(db, owner, {"notification_types": {f"{plugin}.release": False}})
        assert not (await call(db, source, "notifications.emit", event(plugin, dedupe_key="off")))[
            "created"
        ]
        catalog = await routing_settings(db, owner)
        entry = next(item for item in catalog["types"] if item["event_type"] == f"{plugin}.release")
        assert entry["available"] and entry["preference_key"] == ""


async def test_inactive_preferences_remain_visible_without_inbox_history(source):
    owner, plugin, _ = source
    async with SessionLocal() as db:
        await call(db, source, "notification_sources.register", definition(plugin))
        await save_preferences(db, owner, {"notification_types": {f"{plugin}.release": False}})
        await call(
            db, source, "notification_sources.unregister", {"event_type": f"{plugin}.release"}
        )
        entry = next(
            item
            for item in (await routing_settings(db, owner))["types"]
            if item["event_type"] == f"{plugin}.release"
        )
        assert not entry["available"]
        assert not list(
            await db.scalars(
                select(NotificationReceipt).where(NotificationReceipt.user_id == owner)
            )
        )


async def test_legacy_sender_is_installation_bound_and_retires_without_registered_type(source):
    owner, plugin, installation = source
    async with SessionLocal() as db:
        db.add(
            PluginPermissionGrant(
                plugin_id=plugin,
                installation_id=installation,
                user_id=owner,
                capability="notifications.send",
                capability_version=1,
            )
        )
        db.add(endpoint(owner, provider_id="unavailable.transport", endpoint_key="legacy-source"))
        db.add(
            NotificationProviderSetting(
                user_id=owner, provider_id="unavailable.transport", enabled=True
            )
        )
        await db.commit()
        result = await dispatch_gateway_request(
            db,
            plugin_id=plugin,
            installation_id=installation,
            user_id=owner,
            method="notifications.send",
            capability="notifications.send",
            payload={"title": "Legacy source", "body": "Private legacy notice"},
        )
        notice = await db.get(Notification, UUID(result["notification_id"]))
        assert notice.source_installation_id == installation
        assert notice.event_type == "plugin.notice" and await source_delivery_allowed(db, notice)
        await retire_sources(db, plugin)
        await db.commit()
        work = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice.id,
                NotificationDelivery.provider_id == "unavailable.transport",
            )
        )
        assert work.status == "suppressed"


async def test_retirement_preserves_history_and_cannot_replay_on_reinstall(source):
    owner, plugin, installation = source
    async with SessionLocal() as db:
        await call(db, source, "notification_sources.register", definition(plugin))
        target = endpoint(
            owner, Trust.PRIVATE, provider_id="unavailable.transport", endpoint_key="source-test"
        )
        db.add(target)
        db.add(
            NotificationProviderSetting(user_id=owner, provider_id=target.provider_id, enabled=True)
        )
        await db.commit()
        facts = event(plugin)
        result = await call(db, source, "notifications.emit", facts)
        notice = await db.get(Notification, UUID(result["notification_id"]))
        assert await source_delivery_allowed(db, notice)
        await retire_sources(db, plugin)
        await db.commit()
        assert not await source_delivery_allowed(db, notice)
        work = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notice.id,
                NotificationDelivery.provider_id == target.provider_id,
            )
        )
        assert work.status == "suppressed" and work.last_error == "source_revoked"
        assert (await db.get(Notification, notice.id)).body == "Released 3 items."
        assert not next(
            item
            for item in (await routing_settings(db, owner))["types"]
            if item["event_type"] == notice.event_type
        )["available"]
        replacement = uuid4()
        for capability in ("notifications.emit", "notification_sources.register"):
            db.add(
                PluginPermissionGrant(
                    plugin_id=plugin,
                    installation_id=replacement,
                    user_id=owner,
                    capability=capability,
                    capability_version=1,
                )
            )
        await db.commit()
        new_source = owner, plugin, replacement
        await call(db, new_source, "notification_sources.register", definition(plugin))
        assert not (await call(db, new_source, "notifications.emit", facts))["created"]
        assert not await source_delivery_allowed(db, notice)
        assert (await call(db, new_source, "notifications.emit", event(plugin, dedupe_key="new")))[
            "created"
        ]


async def test_emit_revocation_stops_builtin_transport_before_lookup(source, monkeypatch):
    owner, plugin, installation = source
    async with SessionLocal() as db:
        await call(db, source, "notification_sources.register", definition(plugin))
        db.add(endpoint(owner, provider_id="core.smtp", endpoint_key="source-test"))
        db.add(NotificationProviderSetting(user_id=owner, provider_id="core.smtp", enabled=True))
        await db.commit()
        await call(db, source, "notifications.emit", event(plugin))
        grant = await db.scalar(
            select(PluginPermissionGrant).where(
                PluginPermissionGrant.installation_id == installation,
                PluginPermissionGrant.capability == "notifications.emit",
            )
        )
        grant.revoked_at = int(time.time())
        await db.commit()

        async def forbidden_lookup(*args):
            raise AssertionError("Transport lookup must not see revoked source work")

        monkeypatch.setattr(
            "src.features.notification_providers.delivery.get_notification_providers",
            forbidden_lookup,
        )
        claim = await _claim(db)
        assert claim is not None
        assert not await _dispatch(db, *claim)
        assert (await db.get(NotificationDelivery, claim[0])).last_error == "source_revoked"


async def test_quota_uses_server_acceptance_not_backdated_facts(source):
    owner, plugin, _ = source
    async with SessionLocal() as db:
        await call(db, source, "notification_sources.register", definition(plugin))
        facts = event(plugin, occurred_at=int(time.time()) - 10000)
        await call(db, source, "notifications.emit", facts)
        for number in range(99):
            db.add(
                NotificationReceipt(
                    user_id=owner,
                    dedupe_key=f"quota-{number}",
                    event_type=f"{plugin}.release",
                    source=plugin,
                    occurred_at=1,
                )
            )
        await db.commit()
        assert not (await call(db, source, "notifications.emit", facts))["created"]
        with pytest.raises(ValueError, match="quota"):
            await call(db, source, "notifications.emit", {**facts, "dedupe_key": "one-too-many"})


async def test_recovery_source_reaches_only_verified_opted_in_email(source, mailbox):
    owner, plugin, installation = source
    async with SessionLocal() as db:
        db.add(
            PluginPermissionGrant(
                plugin_id=plugin,
                installation_id=installation,
                capability="notifications.sensitive",
                capability_version=1,
                user_id=owner,
            )
        )
        await db.commit()
        target = await create_email(db, owner, "recovery-demo@example.test", "Recovery demo")
        await call(
            db,
            source,
            "notification_sources.register",
            definition(plugin, required_trust="SECURE", purpose="recovery"),
        )
        await call(db, source, "notifications.emit", event(plugin, dedupe_key="unverified"))
        assert await process_pending_deliveries(db) == 0
        assert not mailbox
        await verify(db, owner, target, mailbox)
        await call(db, source, "notifications.emit", event(plugin, dedupe_key="not-opted-in"))
        assert await process_pending_deliveries(db) == 0
        await update_email(db, owner, target, {"recovery_allowed": True})
        result = await call(db, source, "notifications.emit", event(plugin, dedupe_key="eligible"))
        assert await process_pending_deliveries(db) == 1
        assert mailbox[-1]["To"] == "recovery-demo@example.test"
        assert "Released 3 items." in mailbox[-1].get_payload()
        assert not mailbox[-1]["List-Unsubscribe"]
        notice = await db.get(Notification, UUID(result["notification_id"]))
        assert not notice.inbox_visible
