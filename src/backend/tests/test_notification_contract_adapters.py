"""Canonical contract adapters against existing records, producers, providers and dispatch."""

import hashlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from src.api.routes.notifications import dismiss_notification, mark_read, mark_unread
from src.core.preferences import DEFAULTS, load_preferences, save_preferences
from src.database.models.notification import Notification
from src.database.models.notification_audit import NotificationLifecycleOutbox
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_delivery_attempt import NotificationDeliveryAttempt
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import notification_contract as contract
from src.features.notification_contract import (
    BUILTIN_PROFILES,
    DELIVERY_INITIAL_STATUSES,
    DELIVERY_TRANSITIONS,
    INBOX_OPERATIONS,
    LEGACY_ADAPTERS,
    ItemState,
    content_from_draft,
    content_from_notification,
    emission_event,
    inbox_retention_cutoff,
    item_state,
    legacy_text,
    provider_capability_profile,
    source_receipt_key,
)
from src.features.notification_controller import NotificationDraft, emit_legacy_rows
from src.features.notification_lifecycle import delete_notice, retire_destination_work
from src.features.notification_policy import INBOX_PROVIDER, MEDIA_KINDS
from src.features.notification_providers.base import DeliveryResult, notification_message
from src.features.notification_providers.browser import BrowserNotificationProvider
from src.features.notification_providers.delivery import _claim, _dispatch
from src.features.notification_providers.plugin import PluginNotificationProvider
from src.features.notification_providers.smtp import SmtpNotificationProvider
from src.features.notification_providers.webhook import discord_payload
from src.features.notifications import generate_for_user
from src.plugin_api.base_contracts import PLUGIN_API_CONTRACT_VERSION, parse_semver
from src.plugin_api.contracts import NotificationDeliveryRepresentation
from src.plugin_api.gateway import _METHOD_CAPABILITIES
from src.plugin_api.notification_contracts import (
    NotificationEventEmission,
    NotificationFieldLayout,
    NotificationTypeRegistration,
    PluginNotificationContent,
)
from src.plugin_api.notification_story_contracts import (
    NOTIFICATION_CONTRACT_VERSION,
    NotificationContent,
    NotificationProviderCapabilities,
)
from tests.test_notification_core import Transport, queued_work
from tests.test_notification_core import account as account
from tests.test_notification_sources import call, definition, event
from tests.test_notification_sources import source as source

# Every kind an existing host or plugin producer persists today.
PRODUCER_KINDS = {
    "episode_aired": ("tv", "Episode 3 aired", "Example Show S01E03 aired."),
    "season_started": ("tv", "Season 2 started", "Example Show season 2 premiered."),
    "sequel_announced": ("movie", "Sequel announced", "Example Film 2 was announced."),
    "movie_released": ("movie", "Movie released", "Example Film is out."),
    "game_released": ("game", "Game released", "Example Game is out."),
    "game_sale": ("game", "Game on sale", "Example Game is 50% off (EUR 9.99)."),
    "game_price_hit": ("game", "Price target reached", "Example Game is EUR 9.99."),
    "session_anomaly": ("session", "New sign-in", "A new session was created."),
    "plugin_update": ("plugin", "Plugin update available: x", "Version 2.0.0 is available."),
    "plugin": ("plugin", "Plugin notice", "A plugin sent this notice."),
    "user_invite": ("auth_invitation", "You have been invited", "An invitation was issued."),
    "password_reset": ("auth_recovery", "Reset your password", "A reset was requested."),
    "destination_verification": ("notification_destination", "Verify", "A code was requested."),
    "provider_test": ("notification_destination", "Test", "A test notification."),
}


def stored(kind, **changes):
    media_type, title, body = PRODUCER_KINDS[kind]
    fields = dict(
        id=uuid4(),
        user_id=uuid4(),
        kind=kind,
        event_type=f"legacy.{kind}",
        media_type=media_type,
        media_id=uuid4(),
        title=title,
        body=body,
        poster_url="https://image.tmdb.example/poster.jpg" if kind in MEDIA_KINDS else None,
        public_title=title if kind in MEDIA_KINDS else None,
        public_body=body if kind in MEDIA_KINDS else None,
        event_at=1_767_225_600,
        purpose="standard",
        deleted_at=None,
    )
    return Notification(**{**fields, **changes})


@pytest.mark.parametrize("kind", sorted(PRODUCER_KINDS))
def test_every_existing_producer_kind_maps_to_canonical_content(kind):
    notification = stored(kind)
    projections = ["canonical", *(contract.PUBLIC_PROJECTIONS if kind in MEDIA_KINDS else ())]
    for projection in projections:
        content = content_from_notification(notification, projection)
        message = notification_message(notification, projection=projection)
        # The legacy downgrade reproduces exactly what the dispatcher hands providers today.
        assert legacy_text(content) == (message.title, message.body)
        serialized = content.model_dump_json()
        # Raw provider poster URLs never reach canonical content; host media routes do.
        assert "image.tmdb.example" not in serialized
        if projection in contract.PUBLIC_PROJECTIONS:
            assert content.images == () and str(notification.media_id) not in serialized
    canonical = content_from_notification(notification)
    if kind in MEDIA_KINDS:
        assert canonical.images[0].source.media_id == notification.media_id
    else:
        assert canonical.images == ()


def test_legacy_adapter_is_total_for_hostile_and_tombstoned_rows():
    hostile = stored(
        "plugin",
        title="Line one\nLine two\x00" + "x" * 600,
        body="Windows\r\nbreak\x1b[31m and bell\x07",
        poster_url=None,
    )
    content = content_from_notification(hostile)
    assert "\n" not in content.title and len(content.title) == 500
    assert content.summary == "Windows\nbreak [31m and bell "
    blank = content_from_notification(stored("plugin", title="\n\n", body="  "))
    assert blank.title == "Notification" and blank.summary == "Notification"
    with pytest.raises(ValueError, match="no content"):
        content_from_notification(stored("plugin", deleted_at=1, title="", body=""))
    with pytest.raises(ValueError, match="projection"):
        content_from_notification(stored("plugin"), "raw")


async def test_persisted_producer_rows_and_drafts_agree(account):
    media_id = uuid4()
    rows = [
        dict(
            kind="episode_aired",
            media_type="tv",
            media_id=media_id,
            title="Episode aired",
            body="Example S01E01 aired.",
            poster_url="https://posters.example/a.jpg",
            event_at=int(time.time()),
            dedupe_key=f"contract:{uuid4()}",
        ),
        dict(
            kind="plugin",
            media_type="plugin",
            media_id=uuid4(),
            title="Plugin notice",
            body="Body",
            event_at=int(time.time()),
            dedupe_key=f"contract:{uuid4()}",
        ),
    ]
    async with SessionLocal() as db:
        identities = await emit_legacy_rows(db, account, rows)
        await db.commit()
        for identity, row in zip(identities, rows, strict=True):
            notification = await db.get(Notification, identity)
            draft = NotificationDraft(
                kind=row["kind"],
                user_id=account,
                entity_type=row["media_type"],
                entity_id=row["media_id"],
                occurred_at=row["event_at"],
                dedupe_key=row["dedupe_key"],
                title=row["title"],
                body=row["body"],
                poster_url=row.get("poster_url"),
            )
            assert content_from_draft(draft) == content_from_notification(notification)


def emission_fixture(**changes):
    registration = NotificationTypeRegistration.model_validate(
        definition("example.contract", **changes.pop("definition", {}))
    )
    return NotificationEventEmission.model_validate(event("example.contract", **changes)), (
        registration
    )


def test_registered_source_emission_maps_to_a_canonical_event():
    emission, registration = emission_fixture()
    canonical = emission_event(emission, registration)
    assert canonical.contract_version == NOTIFICATION_CONTRACT_VERSION
    assert canonical.event_id == "release-1" and canonical.grouping_key == "releases"
    assert canonical.story is None and canonical.urgency == "normal"
    assert (canonical.content.title, canonical.content.summary) == (
        "Release: Example",
        "Released 3 items.",
    )
    # Parameters are interpolated by host templates; no duplicate structured facts.
    assert canonical.content.facts == () and canonical.content.images == ()
    long, wide = emission_fixture(
        definition={"body_template": "{title}" * 11}, data={"title": "x" * 1000, "count": 1}
    )
    with pytest.raises(ValueError, match="exceeds its bounds"):
        emission_event(long, wide)


async def test_gateway_emission_persists_what_the_adapter_describes(source):
    owner, plugin, _installation = source
    async with SessionLocal() as db:
        await call(db, source, "notification_sources.register", definition(plugin))
        payload = event(plugin)
        result = await call(db, source, "notifications.emit", payload)
        notification = await db.get(Notification, UUID(result["notification_id"]))
        canonical = emission_event(
            NotificationEventEmission.model_validate(payload),
            NotificationTypeRegistration.model_validate(definition(plugin)),
        )
        assert (notification.title, notification.body) == legacy_text(canonical.content)
        assert notification.group_key == f"{payload['event_type']}:{canonical.grouping_key}"
        receipt = await db.scalar(
            select(NotificationReceipt).where(NotificationReceipt.user_id == owner)
        )
        key = source_receipt_key(plugin, payload["event_type"], canonical.event_id)
        assert receipt.dedupe_key == notification.dedupe_key == key
        legacy = hashlib.sha256(f"{plugin}:{payload['event_type']}".encode()).hexdigest()
        assert key == f"src:{legacy}:{payload['dedupe_key']}"


def test_rich_content_downgrades_deterministically_for_legacy_dtos():
    content = NotificationContent.model_validate(
        {
            "title": "Sale",
            "summary": "Two games are discounted.",
            "blocks": [{"type": "markdown", "markdown": "**Example** is cheaper."}],
            "facts": [
                {
                    "key": "price",
                    "label": "Price",
                    "value": {"kind": "money", "amount": "9.99", "currency": "EUR"},
                },
                {
                    "key": "ends",
                    "label": "Ends",
                    "value": {"kind": "timestamp", "value": 1767225600},
                },
                {"key": "owned", "label": "Owned", "value": {"kind": "boolean", "value": False}},
                {"key": "cut", "label": "Discount", "value": {"kind": "decimal", "value": "0.5"}},
            ],
            "images": [{"source": {"kind": "external", "url": "https://img.example.org/a.png"}}],
            "actions": [
                {
                    "id": "store",
                    "label": "Store",
                    "target": {"kind": "link", "url": "https://store.example.org/a"},
                }
            ],
        }
    )
    title, body = legacy_text(content)
    assert body == (
        "Two games are discounted.\n\n**Example** is cheaper.\n\nPrice: 9.99 EUR\n"
        "Ends: 2026-01-01 00:00 UTC\nOwned: No\nDiscount: 0.5"
    )
    # Legacy DTOs never carried producer URLs; the host keeps supplying its own link.
    assert "example.org" not in body
    assert legacy_text(content) == (title, body)
    PluginNotificationContent(
        notification_id=uuid4(), event_type="example.sale", title=title, body=body, event_at=1
    )
    NotificationDeliveryRepresentation(
        notification_id=uuid4(),
        kind="plugin",
        title=title,
        body=body,
        media_type="plugin",
        media_id=uuid4(),
        event_at=1,
    )


def registration(transport, critical=False):
    definition_value = None
    if transport == "plugin":
        definition_value = {
            "destinations": [{"kind": "chat", "label": "Chat", "privacy": "PRIVATE"}],
            "configure_action": "configure",
            "retire_action": "retire",
            "features": {"critical_supported": critical, "critical_description": "Pings."},
        }
    return SimpleNamespace(
        id=uuid4(),
        plugin_id="example.provider",
        installation_id=uuid4(),
        provider_id=f"example.provider.{transport}",
        name="Example",
        action_id="deliver",
        transport=transport,
        definition=definition_value,
        server_revision=1,
        server_configured=False,
    )


@pytest.mark.parametrize(
    "provider",
    [
        SmtpNotificationProvider(),
        BrowserNotificationProvider(),
        *(
            PluginNotificationProvider(registration(transport, critical), runtime=object())
            for transport, critical in (
                ("legacy", False),
                ("discord_webhook", False),
                ("plugin", False),
                ("plugin", True),
            )
        ),
    ],
    ids=["smtp", "browser", "legacy", "discord_webhook", "plugin", "plugin_critical"],
)
def test_existing_providers_have_discoverable_legacy_profiles(provider):
    profile = provider_capability_profile(
        provider.id,
        transport=getattr(provider, "transport", None),
        critical_supported=provider.critical_supported,
    )
    assert profile.renderer == "legacy"
    assert profile.critical_supported is provider.critical_supported
    capabilities = profile.capabilities
    # Current transports deliver one item, cannot edit and receive plain text only.
    assert capabilities.max_batch_items == 1 and capabilities.message_updates == "none"
    assert capabilities.text_formats == ("plain",) and capabilities.action_buttons == 0
    assert not capabilities.live_page_links and capabilities.images is None


def test_profiles_match_existing_transport_limits():
    long = notification_message(stored("plugin", title="t" * 500, body="b" * 9000))
    payload = discord_payload(NotificationFieldLayout(), long, "https://app.example.org")
    discord = contract.TRANSPORT_PROFILES["discord_webhook"].capabilities
    assert len(payload["embeds"][0]["title"]) == discord.max_title_chars
    assert len(payload["embeds"][0]["description"]) == discord.max_text_chars
    plugin = contract.TRANSPORT_PROFILES["plugin"].capabilities
    fields = PluginNotificationContent.model_fields
    assert fields["title"].metadata[-1].max_length == plugin.max_title_chars
    assert fields["body"].metadata[-1].max_length == plugin.max_text_chars
    assert "link" in fields and plugin.links
    assert "link" not in NotificationDeliveryRepresentation.model_fields
    assert not contract.TRANSPORT_PROFILES["legacy"].capabilities.links
    assert BUILTIN_PROFILES["core.browser"].generic_preview_only
    assert BUILTIN_PROFILES["core.smtp"].capabilities.links
    assert BUILTIN_PROFILES[INBOX_PROVIDER].capabilities.images.proxied_urls


def test_only_generic_plugin_providers_declare_canonical_capabilities():
    declared = NotificationProviderCapabilities(
        contract_version=NOTIFICATION_CONTRACT_VERSION, max_batch_items=10, links=True
    )
    profile = provider_capability_profile("x.y", transport="plugin", declared=declared)
    assert profile.renderer == "canonical" and profile.capabilities.max_batch_items == 10
    with pytest.raises(ValueError, match="generic plugin"):
        provider_capability_profile("x.y", transport="discord_webhook", declared=declared)
    with pytest.raises(ValueError, match="transport"):
        provider_capability_profile("x.y", transport="smtp")
    # Built-in identities cannot be replaced by a declaration.
    assert (
        provider_capability_profile("core.smtp", declared=declared) is BUILTIN_PROFILES["core.smtp"]
    )


def test_every_notification_operation_has_an_explicit_adapter():
    operations = {name for name in _METHOD_CAPABILITIES if "notification" in name}
    runtime = {"notification-deliveries", "notification-layouts", "notification-story-actions"}
    assert set(LEGACY_ADAPTERS) == operations | runtime
    for adapter in LEGACY_ADAPTERS.values():
        assert parse_semver(adapter.introduced_in) <= parse_semver(PLUGIN_API_CONTRACT_VERSION)
        names = adapter.adapter.split("/")
        assert adapter.adapter == "unchanged" or all(hasattr(contract, name) for name in names)


@pytest.fixture
async def inbox_user():
    user_id = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id, username=user_id.hex, email=f"{user_id}@example.test", password_hash="x"
            )
        )
        await db.commit()
        user = await db.get(User, user_id)
    yield user
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


async def apply(db, user, name, notice):
    handlers = {"read": mark_read, "unread": mark_unread, "dismiss": dismiss_notification}
    if name == "delete":
        await delete_notice(db, user.id, notice.id)
        await db.commit()
    else:
        await handlers[name](notice.id, db=db, current_user=user)


async def recorded(db, notice):
    return list(
        await db.scalars(
            select(NotificationLifecycleOutbox.status)
            .where(NotificationLifecycleOutbox.notification_id == notice.id)
            .order_by(NotificationLifecycleOutbox.position)
        )
    )


async def test_inbox_operations_follow_the_contract_table(inbox_user):
    """Real routes produce exactly the documented item state and replay status."""
    async with SessionLocal() as db:
        for sequence in (
            ["read", "unread", "read", "dismiss", "delete"],
            ["dismiss", "delete"],
            ["unread", "delete"],
        ):
            notice = Notification(
                user_id=inbox_user.id,
                kind="plugin",
                event_type="example.contract.notice",
                source="example.contract",
                source_installation_id=uuid4(),
                media_type="plugin",
                media_id=uuid4(),
                title="Contract",
                body="Body",
                event_at=int(time.time()),
                dedupe_key=str(uuid4()),
                inbox_visible=True,
            )
            db.add(notice)
            await db.commit()
            for name in sequence:
                operation = INBOX_OPERATIONS[name]
                assert item_state(notice) in operation.sources
                await apply(db, inbox_user, name, notice)
                await db.refresh(notice)
                assert item_state(notice) == operation.target
                assert (await recorded(db, notice))[-1] == operation.lifecycle_status
            # Operations outside the table change nothing through the existing routes.
            history = await recorded(db, notice)
            for name in ("read", "unread"):
                assert item_state(notice) not in INBOX_OPERATIONS[name].sources
                with pytest.raises(HTTPException):
                    await apply(db, inbox_user, name, notice)
            await apply(db, inbox_user, "dismiss", notice)
            await db.refresh(notice)
            assert item_state(notice) == ItemState.DELETED
            assert await recorded(db, notice) == history


async def test_retention_expiry_and_refused_transitions(inbox_user):
    now = int(time.time())
    async with SessionLocal() as db:
        await save_preferences(
            db,
            inbox_user.id,
            {"notification_retention_inherit": False, "notification_retention_days": 7},
        )
        prefs = await load_preferences(db, inbox_user.id)
        cutoff = inbox_retention_cutoff(prefs, now)
        assert cutoff == now - 7 * 86400
        assert (
            inbox_retention_cutoff(
                {
                    **DEFAULTS,
                    "notification_retention_inherit": False,
                    "notification_retention_days": 0,
                },
                now,
            )
            is None
        )
        old, dismissed = (
            Notification(
                user_id=inbox_user.id,
                kind="plugin",
                event_type="example.contract.notice",
                source="example.contract",
                source_installation_id=uuid4(),
                media_type="plugin",
                media_id=uuid4(),
                title="Old",
                body="Body",
                event_at=now - 8 * 86400,
                dedupe_key=str(uuid4()),
                inbox_visible=True,
                dismissed_at=dismissed_at,
            )
            for dismissed_at in (None, now)
        )
        db.add_all([old, dismissed])
        await db.commit()
        assert item_state(old, retention_cutoff=cutoff) == ItemState.EXPIRED
        identities = old.id, dismissed.id
        await generate_for_user(db, inbox_user.id)
        for identity in identities:
            assert await db.get(Notification, identity) is None
            statuses = list(
                await db.scalars(
                    select(NotificationLifecycleOutbox.status).where(
                        NotificationLifecycleOutbox.notification_id == identity
                    )
                )
            )
            assert statuses == [INBOX_OPERATIONS["expire"].lifecycle_status]
        assert INBOX_OPERATIONS["expire"].sources >= {ItemState.UNREAD, ItemState.DISMISSED}
        dismissed_notice = Notification(
            user_id=inbox_user.id,
            kind="plugin",
            event_type="example.contract.notice",
            source="example.contract",
            media_type="plugin",
            media_id=uuid4(),
            title="Dismissed",
            body="Body",
            event_at=now,
            dedupe_key=str(uuid4()),
            inbox_visible=True,
            dismissed_at=now,
        )
        db.add(dismissed_notice)
        await db.commit()
        assert ItemState.DISMISSED not in INBOX_OPERATIONS["read"].sources
        with pytest.raises(HTTPException):
            await mark_read(dismissed_notice.id, db=db, current_user=inbox_user)


class Observer:
    """Record every observed status pair for one delivery."""

    def __init__(self):
        self.pairs = set()
        self.last = {}

    async def see(self, db, delivery_id):
        status = await db.scalar(
            select(NotificationDelivery.status)
            .where(NotificationDelivery.id == delivery_id)
            .execution_options(populate_existing=True)
        )
        previous = self.last.get(delivery_id)
        if previous is None:
            assert status in DELIVERY_INITIAL_STATUSES
        elif previous != status or status == "processing":
            self.pairs.add((previous, status))
        self.last[delivery_id] = status
        return status


async def external(db, identity, provider):
    return await db.scalar(
        select(NotificationDelivery.id).where(
            NotificationDelivery.notification_id == identity,
            NotificationDelivery.provider_id == provider,
        )
    )


async def test_dispatcher_transitions_stay_inside_the_contract_table(account, monkeypatch):
    transport = Transport(DeliveryResult(success=False, retryable=True, error="later"))
    monkeypatch.setattr(
        "src.features.notification_providers.delivery.get_notification_providers",
        AsyncMock(return_value={"a": transport}),
    )
    observer = Observer()
    async with SessionLocal() as db:
        observer.identity = await queued_work(db, account, {"a": transport})
        inbox = await db.scalar(
            select(NotificationDelivery.id).where(
                NotificationDelivery.notification_id == observer.identity,
                NotificationDelivery.provider_id == INBOX_PROVIDER,
            )
        )
        assert await observer.see(db, inbox) == "sent"
        work = await external(db, observer.identity, "a")
        assert await observer.see(db, work) == "pending"
        # A worker crashed after starting attempt 1: its lease expires and is reclaimed.
        stale = await _claim(db)
        assert await observer.see(db, work) == "processing"
        lease = await db.get(NotificationDelivery, work)
        lease.attempts, lease.lease_until = 1, 0
        db.add(
            NotificationDeliveryAttempt(
                delivery_id=work, number=1, claim_token=stale[1], started_at=int(time.time())
            )
        )
        await db.commit()
        current = await _claim(db)
        assert current[1] != stale[1] and await observer.see(db, work) == "processing"
        await _dispatch(db, *current)
        assert await observer.see(db, work) == "retry_wait"
        row = await db.get(NotificationDelivery, work, populate_existing=True)
        row.next_attempt_at = 0
        await db.commit()
        current = await _claim(db)
        assert await observer.see(db, work) == "processing"
        await _dispatch(db, *current)
        assert await observer.see(db, work) == "failed_permanent"
        outcomes = set(
            await db.scalars(
                select(NotificationDeliveryAttempt.outcome).where(
                    NotificationDeliveryAttempt.delivery_id == work
                )
            )
        )
        assert outcomes <= contract.ATTEMPT_TRANSITIONS["processing"]
        assert outcomes == {"unknown", "failed"}
        # Retirement and deletion of other queued work.
        transport.outcome = DeliveryResult(success=True)
        second = Observer()
        second.identity = await queued_work(db, account, {"b": transport})
        retired = await external(db, second.identity, "b")
        await second.see(db, retired)
        await retire_destination_work(
            db, (await db.get(NotificationDelivery, retired)).destination_id, "contract"
        )
        await db.commit()
        assert await second.see(db, retired) == "suppressed"
        third = Observer()
        third.identity = await queued_work(db, account, {"c": transport})
        cancelled = await external(db, third.identity, "c")
        await third.see(db, cancelled)
        await delete_notice(db, account, third.identity)
        await db.commit()
        assert await third.see(db, cancelled) == "cancelled"
    for pair in observer.pairs | second.pairs | third.pairs:
        assert pair[1] in DELIVERY_TRANSITIONS[pair[0]], pair


async def test_successful_dispatch_reaches_terminal_sent(account, monkeypatch):
    transport = Transport()
    monkeypatch.setattr(
        "src.features.notification_providers.delivery.get_notification_providers",
        AsyncMock(return_value={"a": transport}),
    )
    observer = Observer()
    async with SessionLocal() as db:
        observer.identity = await queued_work(db, account, {"a": transport})
        work = await external(db, observer.identity, "a")
        await observer.see(db, work)
        claim = await _claim(db)
        await observer.see(db, work)
        assert await _dispatch(db, *claim)
        assert await observer.see(db, work) == "sent"
        assert DELIVERY_TRANSITIONS["sent"] == frozenset()
    assert observer.pairs == {("pending", "processing"), ("processing", "sent")}
