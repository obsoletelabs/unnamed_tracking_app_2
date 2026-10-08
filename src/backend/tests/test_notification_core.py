"""Destination routing, transactional acceptance, delivery recovery and game integrations."""

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from src.core.preferences import DEFAULTS, save_preferences
from src.database.models.game import Game, GameStatus
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_delivery_attempt import NotificationDeliveryAttempt
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.plugin_notification_provider import PluginNotificationProviderRegistration
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.game_notifications import GamePriceObservation
from src.features.notification_controller import NotificationEvent, emit, emit_legacy_rows
from src.features.notification_destinations import invalidate_legacy_configuration
from src.features.notification_lifecycle import delete_notice, dismiss, visible_inbox
from src.features.notification_policy import (
    INBOX_PROVIDER,
    Trust,
    effective_trust,
    select_projection,
)
from src.features.notification_providers.base import DeliveryResult, ProviderDestination
from src.features.notification_providers.delivery import (
    _claim,
    _dispatch,
    process_pending_deliveries,
)
from src.features.notifications import generate_for_user
from src.plugin_api.runtime_client import PluginRuntimeUnavailable


def endpoint(user_id, trust=Trust.PRIVATE, **overrides):
    fields = dict(
        id=uuid4(),
        user_id=user_id,
        provider_id="test.transport",
        endpoint_key=str(uuid4()),
        kind="email",
        privacy=int(trust),
        channel_context="external",
        enabled=True,
        active=True,
        revision=1,
        verified_revision=1 if trust == Trust.SECURE else None,
        verification_method="test.host.proof" if trust == Trust.SECURE else None,
        verification_revoked_at=None,
        recovery_allowed=False,
        installation_id=None,
        media_consent_revision=None,
        media_consent_at=None,
    )
    if trust == Trust.SECURE:
        fields["privacy"] = int(Trust.PRIVATE)
    return NotificationDestination(**{**fields, **overrides})


def notice(user_id, trust, **overrides):
    return Notification(
        user_id=user_id,
        id=uuid4(),
        kind="plugin",
        event_type="plugin.notice",
        required_trust=int(trust),
        purpose="standard",
        deleted_at=None,
        **overrides,
    )


@pytest.mark.parametrize(
    "required,destination,allowed",
    [
        (Trust.PUBLIC, Trust.PUBLIC, True),
        (Trust.PRIVATE, Trust.PUBLIC, False),
        (Trust.PRIVATE, Trust.PRIVATE, True),
        (Trust.SECURE, Trust.PRIVATE, False),
        (Trust.SECURE, Trust.SECURE, True),
    ],
)
def test_routing_trust_matrix(required, destination, allowed):
    user_id = uuid4()
    assert (
        bool(select_projection(notice(user_id, required), endpoint(user_id, destination), DEFAULTS))
        == allowed
    )


def test_proof_revision_revocation_and_recovery_purpose():
    user_id = uuid4()
    target = endpoint(user_id, Trust.SECURE)
    assert effective_trust(target) == Trust.SECURE
    target.revision = 2
    assert effective_trust(target) == Trust.PRIVATE
    target.verified_revision = 2
    target.verification_revoked_at = 1
    assert effective_trust(target) == Trust.PRIVATE
    target.verification_revoked_at = None
    recovery = notice(user_id, Trust.SECURE)
    recovery.purpose = "recovery"
    assert select_projection(recovery, target, DEFAULTS) is None
    target.recovery_allowed = True
    assert select_projection(recovery, target, DEFAULTS) == "canonical"
    target.provider_id = INBOX_PROVIDER
    target.channel_context = "internal"
    assert select_projection(recovery, target, DEFAULTS) is None


def test_public_media_fallback_and_revision_bound_consent_do_not_promote_trust():
    user_id = uuid4()
    target = endpoint(user_id, Trust.PUBLIC, kind="webhook")
    media = notice(user_id, Trust.PRIVATE)
    media.kind = "episode_aired"
    media.public_title, media.public_body = "Show", "Episode 4 aired"
    assert select_projection(media, target, DEFAULTS) == "public_release"
    target.media_consent_revision, target.media_consent_at = 1, 1
    assert select_projection(media, target, DEFAULTS) == "media_shared"
    assert effective_trust(target) == Trust.PUBLIC
    target.revision = 2
    assert select_projection(media, target, DEFAULTS) == "public_release"
    media.kind, media.required_trust = "session_anomaly", int(Trust.SECURE)
    assert select_projection(media, target, DEFAULTS) is None


def test_admin_restrictions_cannot_lower_type_floor_or_override_user_opt_out(monkeypatch):
    from src.core.config import settings

    user_id = uuid4()
    target = endpoint(user_id, Trust.PRIVATE)
    notification = notice(user_id, Trust.SECURE)
    monkeypatch.setattr(settings, "NOTIFICATION_MINIMUM_TRUST", 0)
    assert select_projection(notification, target, DEFAULTS) is None
    target = endpoint(user_id, Trust.SECURE)
    monkeypatch.setattr(settings, "NOTIFICATION_BLOCKED_PROVIDERS", target.provider_id)
    assert select_projection(notification, target, DEFAULTS) is None
    monkeypatch.setattr(settings, "NOTIFICATION_BLOCKED_PROVIDERS", "")
    monkeypatch.setattr(settings, "NOTIFICATION_BLOCKED_TYPES", notification.event_type)
    assert select_projection(notification, target, DEFAULTS) is None
    monkeypatch.setattr(settings, "NOTIFICATION_BLOCKED_TYPES", "")
    assert (
        select_projection(
            notification,
            target,
            {**DEFAULTS, "notification_types": {notification.event_type: False}},
        )
        is None
    )


@pytest.fixture
async def account():
    user_id = uuid4()
    async with SessionLocal() as db:
        user = User(
            id=user_id,
            username=f"notice_{user_id.hex}",
            email=f"{user_id}@example.test",
            password_hash="unused",
        )
        db.add(user)
        await db.commit()
    try:
        yield user_id
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


def legacy_row(kind="plugin"):
    return dict(
        kind=kind,
        media_type="system",
        media_id=uuid4(),
        title="Private title",
        body="Private body",
        event_at=int(time.time()),
        dedupe_key=str(uuid4()),
    )


async def accept(db, account, row=None):
    identities = await emit_legacy_rows(db, account, [row or legacy_row()])
    return identities[0] if identities else None


async def test_acceptance_is_transactional_and_runtime_independent(account, monkeypatch):
    runtime = AsyncMock(side_effect=PluginRuntimeUnavailable("offline"))
    monkeypatch.setattr("src.features.notification_providers.registry.PluginRuntimeClient", runtime)
    row = legacy_row("session_anomaly")
    async with SessionLocal() as db:
        identity = await accept(db, account, row)
        inbox = await db.scalar(
            select(NotificationDelivery).where(NotificationDelivery.notification_id == identity)
        )
        assert inbox.provider_id == INBOX_PROVIDER and inbox.status == "sent"
        notification = await db.get(Notification, identity)
        assert notification.required_trust == Trust.SECURE
        await db.rollback()
    async with SessionLocal() as db:
        assert (
            await db.scalar(
                select(NotificationReceipt).where(NotificationReceipt.user_id == account)
            )
            is None
        )
    runtime.assert_not_called()


async def test_user_opt_out_and_destination_routing_preferences(account):
    async with SessionLocal() as db:
        await save_preferences(db, account, {"notify_session_anomaly": False})
        assert await accept(db, account, legacy_row("session_anomaly")) is None
        await save_preferences(db, account, {"notify_session_anomaly": True})
        identity = await accept(db, account)
        target = await db.scalar(
            select(NotificationDestination).where(NotificationDestination.user_id == account)
        )
        await save_preferences(db, account, {"notification_destinations": {str(target.id): False}})
        second = await accept(db, account)
        assert (await db.get(Notification, identity)).inbox_visible
        assert not (await db.get(Notification, second)).inbox_visible


async def test_shared_configuration_change_invalidates_revision_proof_consent_and_work(account):
    installation_id = uuid4()
    target = endpoint(
        account,
        Trust.PUBLIC,
        kind="legacy_webhook",
        endpoint_key="legacy",
        installation_id=installation_id,
        media_consent_revision=1,
        media_consent_at=1,
        verified_revision=1,
        verification_method="stale",
    )
    other = endpoint(
        account, Trust.PUBLIC, kind="legacy_webhook", endpoint_key="other", installation_id=uuid4()
    )
    async with SessionLocal() as db:
        db.add(
            PluginNotificationProviderRegistration(
                plugin_id="test.plugin",
                provider_id=target.provider_id,
                installation_id=installation_id,
                name="Test",
                action_id="deliver",
            )
        )
        db.add_all([target, other])
        identity = await accept(db, account)
        work = NotificationDelivery(
            notification_id=identity,
            provider_id=target.provider_id,
            destination_id=target.id,
            destination_revision=1,
            status="processing",
            claim_token=uuid4(),
            lease_until=100,
        )
        db.add(work)
        await db.flush()
        await invalidate_legacy_configuration(db, installation_id)
        await db.refresh(target)
        await db.refresh(other)
        await db.refresh(work)
        assert target.revision == 2 and not target.enabled
        assert target.verified_revision is None and target.verification_method is None
        assert target.media_consent_revision is None and target.media_consent_at is None
        assert work.status == "suppressed" and work.claim_token is None
        assert other.revision == 1 and other.enabled


async def test_dedupe_survives_dismiss_delete_and_content_purge(account):
    row = legacy_row()
    async with SessionLocal() as db:
        identity = await accept(db, account, row)
        target = endpoint(account)
        db.add(target)
        await db.flush()
        work = NotificationDelivery(
            notification_id=identity,
            provider_id=target.provider_id,
            destination_id=target.id,
            destination_revision=1,
            status="pending",
        )
        db.add(work)
        await db.commit()
        assert await dismiss(db, account, identity)
        assert work.status == "pending"
        assert await db.scalar(select(Notification).where(visible_inbox(account))) is None
        assert await accept(db, account, row) is None
        assert await delete_notice(db, account, identity)
        await db.refresh(work)
        assert work.status == "cancelled"
        assert (await db.get(Notification, identity)).body == ""
        await db.execute(delete(Notification).where(Notification.id == identity))
        await db.commit()
        assert await accept(db, account, row) is None


async def test_concurrent_event_acceptance_creates_one_notice(account):
    row = legacy_row()

    async def publish():
        async with SessionLocal() as db:
            result = await accept(db, account, row)
            await db.commit()
            return result

    results = await asyncio.gather(publish(), publish())
    assert sum(result is not None for result in results) == 1


class Transport:
    def __init__(self, outcome=None, lookup_error=None):
        self.outcome = outcome or DeliveryResult(success=True)
        self.lookup_error = lookup_error
        self.messages = []

    async def lookup_destination(self, _db, user, _setting):
        if self.lookup_error:
            raise self.lookup_error
        return ProviderDestination(user.id, "Test transport")

    async def deliver(self, db, _target, message):
        # A physical attempt must already be committed before the transport runs.
        async with SessionLocal() as observer:
            attempt = await observer.get(NotificationDeliveryAttempt, message.attempt_id)
            assert attempt is not None and attempt.outcome == "processing"
        self.messages.append(message)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


async def queued_work(db, account, providers):
    for name in providers:
        db.add(endpoint(account, provider_id=name))
    await db.flush()
    identity = await accept(db, account)
    await db.commit()
    return identity


async def test_provider_failure_isolated_retries_and_failures_persist(account, monkeypatch):
    failed, succeeded = Transport(RuntimeError("secret must not persist")), Transport()
    providers = {"a": failed, "b": succeeded}
    monkeypatch.setattr(
        "src.features.notification_providers.delivery.get_notification_providers",
        AsyncMock(return_value=providers),
    )
    async with SessionLocal() as db:
        identity = await queued_work(db, account, providers)
        assert await process_pending_deliveries(db) == 1
        deliveries = list(
            await db.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == identity,
                    NotificationDelivery.provider_id != INBOX_PROVIDER,
                )
            )
        )
        failure = next(work for work in deliveries if work.provider_id == "a")
        assert failure.status == "retry_wait" and failure.attempts == 1
        assert failure.last_error == "provider_error"
        for _ in range(2):
            failure.next_attempt_at = 0
            await db.commit()
            await process_pending_deliveries(db)
            await db.refresh(failure)
        assert failure.status == "failed_permanent" and failure.attempts == 3
        assert (await db.get(Notification, identity)).body == "Private body"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(NotificationDeliveryAttempt)
                .where(NotificationDeliveryAttempt.delivery_id == failure.id)
            )
            == 3
        )


async def test_runtime_outage_waits_without_consuming_transport_attempt(account, monkeypatch):
    transport = Transport(lookup_error=PluginRuntimeUnavailable("offline"))
    monkeypatch.setattr(
        "src.features.notification_providers.delivery.get_notification_providers",
        AsyncMock(return_value={"a": transport}),
    )
    async with SessionLocal() as db:
        identity = await queued_work(db, account, {"a": transport})
        await process_pending_deliveries(db)
        delivery = await db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == identity,
                NotificationDelivery.provider_id == "a",
            )
        )
        assert delivery.status == "retry_wait" and delivery.attempts == 0
        assert transport.messages == []


async def test_expired_lease_reclaimed_and_stale_worker_cannot_send(account, monkeypatch):
    transport = Transport()
    monkeypatch.setattr(
        "src.features.notification_providers.delivery.get_notification_providers",
        AsyncMock(return_value={"a": transport}),
    )
    async with SessionLocal() as db:
        await queued_work(db, account, {"a": transport})
        stale = await _claim(db)
        work = await db.get(NotificationDelivery, stale[0])
        work.lease_until = 0
        await db.commit()
        current = await _claim(db)
        assert current[0] == stale[0] and current[1] != stale[1]
        assert not await _dispatch(db, *stale)
        assert await _dispatch(db, *current)
        assert len(transport.messages) == 1


async def test_destination_revision_change_blocks_queued_delivery(account, monkeypatch):
    transport = Transport()
    monkeypatch.setattr(
        "src.features.notification_providers.delivery.get_notification_providers",
        AsyncMock(return_value={"a": transport}),
    )
    async with SessionLocal() as db:
        await queued_work(db, account, {"a": transport})
        target = await db.scalar(
            select(NotificationDestination).where(
                NotificationDestination.user_id == account,
                NotificationDestination.provider_id == "a",
            )
        )
        target.revision += 1
        await db.commit()
        assert await process_pending_deliveries(db) == 0
        assert transport.messages == []


async def test_delete_during_lookup_does_not_release_transport_work(account, monkeypatch):
    transport = Transport()
    monkeypatch.setattr(
        "src.features.notification_providers.delivery.get_notification_providers",
        AsyncMock(return_value={"a": transport}),
    )
    async with SessionLocal() as db:
        identity = await queued_work(db, account, {"a": transport})

        async def delete_on_lookup(_db, user, _setting):
            async with SessionLocal() as other:
                await delete_notice(other, account, identity)
                await other.commit()
            return ProviderDestination(user.id, "Test")

        transport.lookup_destination = delete_on_lookup
        assert await process_pending_deliveries(db) == 0
        assert transport.messages == []


async def owned_game(db, account):
    game = Game(
        user_id=account,
        title="Example Game",
        sort_title="example",
        folder_location=uuid4().hex,
        status=GameStatus.WISHLIST,
        release_date=datetime.now(timezone.utc).date(),
    )
    db.add(game)
    await db.flush()
    return game


async def test_game_release_sale_and_price_hit_have_owned_enrichment_and_stable_dedupe(account):
    async with SessionLocal() as db:
        game = await owned_game(db, account)
        game.title = "Narnia chronicles"
        game.locked_fields = ["title"]
        assert await generate_for_user(db, account) == 1
        assert await generate_for_user(db, account) == 0
        quote = dict(
            source="integration.store",
            observation_id="sale-1",
            currency="USD",
            amount="19.99",
            regular_amount="39.99",
            market="US",
            platform="PC",
        )
        event = NotificationEvent(
            "game.sale.started", account, "game", game.id, int(time.time()), "source-key", quote
        )
        sale = await emit(db, event)
        assert await emit(db, event) is None
        with pytest.raises(ValueError, match="conflicting"):
            await emit(db, replace(event, data={**quote, "amount": "18.99"}))
        assert (await db.get(Notification, sale)).title == game.title
        assert (await db.get(Notification, sale)).public_title == game.title
        hit = replace(
            event,
            type="game.price.threshold_hit",
            data={
                **quote,
                "threshold": "20.00",
                "previous_amount": "25.00",
                "threshold_id": "wish-1",
            },
        )
        identity = await emit(db, hit)
        notification = await db.get(Notification, identity)
        assert "your USD 20.00 target" in notification.body
        assert "target" not in notification.public_body
        with pytest.raises(ValueError, match="owned"):
            await emit(db, replace(event, entity_id=uuid4()))
        with pytest.raises(ValueError, match="crossing"):
            await emit(db, replace(hit, data={**hit.data, "previous_amount": "19.00"}))


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": 19.99},
        {"currency": "usd"},
        {"amount": "NaN"},
        {"amount": "40", "regular_amount": "39.99"},
    ],
)
def test_live_quote_rejects_ambiguous_or_invalid_prices(changes):
    with pytest.raises(ValueError):
        GamePriceObservation.model_validate(
            {
                "source": "store",
                "observation_id": "1",
                "currency": "USD",
                "amount": "19.99",
                "market": "US",
                "platform": "PC",
                **changes,
            }
        )
