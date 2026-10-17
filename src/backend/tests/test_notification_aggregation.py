"""Durable grouping windows, deterministic batches and restart-safe digests."""

import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update

from src.database.models.notification import Notification
from src.database.models.notification_aggregation import NotificationAggregationWindow
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import notification_aggregation_policy as policy
from src.features.notification_aggregation import (
    purge_sealed,
    recover_orphans,
    seal_due_windows,
)
from src.features.notification_controller import emit_legacy_rows
from src.features.notification_lifecycle import delete_notice
from src.features.notification_policy import INBOX_PROVIDER
from src.features.notification_providers.base import (
    DeliveryResult,
    DigestItem,
    ProviderDestination,
)
from src.features.notification_providers.delivery import process_pending_deliveries
from src.features.notification_stories import PLUGIN_UPDATE_LIFECYCLE, StoryReference

PROVIDERS = "src.features.notification_providers.delivery.get_notification_providers"
FAR = 10 * 365 * 86400


def item(index, title="Episode", at=None, **changes):
    return DigestItem(
        id=UUID(int=index),
        kind="episode_aired",
        event_type="media.episode.aired",
        media_type="tv",
        media_id=UUID(int=1),
        event_at=index if at is None else at,
        title=f"{title} {index}",
        body="",
        **changes,
    )


# Policy ---------------------------------------------------------------------------------------


def test_coalescing_window_starts_at_first_member_and_digest_window_is_aligned():
    coalesce = policy.POLICIES["media.episode.aired"]
    assert policy.window_bounds(coalesce, 1_000_007) == (1_000_007, 1_000_007 + 900)
    digest = policy.POLICIES["game.sale.started"]
    start = 1_000_007 - 1_000_007 % (6 * 3600)
    assert policy.window_bounds(digest, 1_000_007) == (start, start + 6 * 3600)
    # Every instant inside one aligned window shares its boundaries.
    assert policy.window_bounds(digest, start) == policy.window_bounds(digest, start + 6 * 3600 - 1)
    assert policy.window_bounds(digest, start + 6 * 3600)[0] == start + 6 * 3600


@pytest.mark.parametrize("purpose", ["security", "recovery", "verification", "invitation", "test"])
def test_only_standard_notifications_can_be_aggregated(purpose):
    result = policy.policy_for(
        event_type="game.sale.started",
        kind="game_sale",
        purpose=purpose,
        group_key=None,
        has_group_hint=False,
    )
    assert result.strategy == "immediate"


def test_policy_resolution_per_category():
    def resolve(event_type, kind="x", group_key=None, hint=False):
        return policy.policy_for(
            event_type=event_type,
            kind=kind,
            purpose="standard",
            group_key=group_key,
            has_group_hint=hint,
        )

    assert resolve("media.episode.aired").scope == "entity"
    assert resolve("game.sale.started").strategy == "digest"
    assert resolve("system.plugin.update").scope == "event_type"
    assert resolve("game.price.threshold_hit").strategy == "immediate"
    assert resolve("example.release", "plugin", "example.release:r", hint=True).scope == "group_key"
    assert resolve("example.release", "plugin", "example.release:r", hint=False).strategy == (
        "immediate"
    )
    assert resolve("example.release", "plugin", None, hint=True).strategy == "immediate"


def test_release_scope_never_merges_entities_or_release_days():
    base = dict(event_type="media.episode.aired", event_at=10 * 86400 + 5, group_key="")
    entity = policy.POLICIES["media.episode.aired"]
    first, second = uuid4(), uuid4()
    same = policy.scope_key(entity, media_id=first, **base)
    assert same == policy.scope_key(entity, media_id=first, **{**base, "event_at": 11 * 86400 - 1})
    assert same != policy.scope_key(entity, media_id=second, **base)
    assert same != policy.scope_key(entity, media_id=first, **{**base, "event_at": 11 * 86400})
    sales = policy.POLICIES["game.sale.started"]
    assert policy.scope_key(sales, media_id=first, **base) == policy.scope_key(
        sales, media_id=second, **{**base, "event_at": 99}
    )


def test_urgent_bypass_only_when_policy_permits():
    coalesce = policy.POLICIES["media.episode.aired"]
    assert policy.bypasses(coalesce, requested_urgency="critical", severity="info")
    assert policy.bypasses(coalesce, requested_urgency="normal", severity="critical")
    assert not policy.bypasses(coalesce, requested_urgency="normal", severity="info")
    strict = replace(coalesce, urgent_bypass=False)
    assert not policy.bypasses(strict, requested_urgency="critical", severity="critical")


def test_chunking_is_deterministic_ordered_and_bounded():
    limits = policy.provider_limits("core.smtp", None)
    items = [item(index) for index in (5, 1, 4, 2, 3)]
    chunks = policy.chunk_items(items, replace(limits, max_items=2))
    assert [[entry.event_at for entry in chunk] for chunk in chunks] == [[1, 2], [3, 4], [5]]
    assert chunks == policy.chunk_items(list(reversed(items)), replace(limits, max_items=2))
    # Equal times are ordered by notification ID.
    tied = [item(3, at=7), item(2, at=7), item(1, at=7)]
    assert [entry.id.int for entry in policy.chunk_items(tied, limits)[0]] == [1, 2, 3]
    assert policy.chunk_items([], limits) == []


def test_chunking_respects_each_providers_text_and_payload_limits():
    items = [item(index, title="x" * 180) for index in range(1, 61)]
    for provider, transport, key in (
        ("core.smtp", None, "max_text_chars"),
        ("core.browser", None, "max_payload_bytes"),
        ("example.discord", "discord_webhook", "max_text_chars"),
    ):
        limits = policy.provider_limits(provider, transport)
        chunks = policy.chunk_items(items, limits)
        assert sum(len(chunk) for chunk in chunks) == 60
        for index, chunk in enumerate(chunks, 1):
            title, body = policy.compose_digest(chunk, limits=limits, part=index, parts=len(chunks))
            assert len(chunk) <= limits.max_items
            assert len(body) <= limits.max_text_chars and len(title) <= limits.max_title_chars
            assert len(body.encode()) <= limits.max_payload_bytes
        assert limits.__dict__[key] > 0
    # A tighter provider needs more messages for the same items.
    assert len(policy.chunk_items(items, policy.provider_limits("core.browser", None))) > len(
        policy.chunk_items(items, policy.provider_limits("core.smtp", None))
    )


def test_composed_digest_keeps_identity_titles_and_part_numbers():
    limits = policy.provider_limits("core.smtp", None)
    title, body = policy.compose_digest(
        [item(2), item(1)], limits=limits, part=2, parts=3, title_of=lambda n: f"{n} new episodes"
    )
    assert title == "2 new episodes (2/3)"
    assert body.splitlines() == ["- Episode 1", "- Episode 2"]


# Persistence ----------------------------------------------------------------------------------


class Transport:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.messages = []

    async def lookup_destination(self, _db, user, _setting):
        return ProviderDestination(user.id, "Test transport")

    async def deliver(self, _db, _target, message):
        self.messages.append(message)
        return self.outcomes.pop(0) if self.outcomes else DeliveryResult(success=True)


@pytest.fixture
async def account():
    user_id = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                username=f"group_{user_id.hex}",
                email=f"{user_id}@example.test",
                password_hash="unused",
            )
        )
        await db.commit()
    try:
        yield user_id
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


async def add_destination(db, account, provider):
    target = NotificationDestination(
        id=uuid4(),
        user_id=account,
        provider_id=provider,
        endpoint_key=str(uuid4()),
        kind="email",
        privacy=1,
        channel_context="external",
        enabled=True,
        active=True,
        revision=1,
        verified_revision=1,
        verification_method="test.host.proof",
    )
    db.add(target)
    db.add(NotificationProviderSetting(user_id=account, provider_id=provider, enabled=True))
    await db.flush()
    return target


def media_row(kind="episode_aired", media_id=None, title="Episode", **changes):
    return {
        "kind": kind,
        "media_type": "tv",
        "media_id": media_id or uuid4(),
        "title": title,
        "body": f"{title} aired",
        "event_at": int(time.time()),
        "dedupe_key": str(uuid4()),
        **changes,
    }


async def emit(account, *rows):
    async with SessionLocal() as db:
        created = await emit_legacy_rows(db, account, list(rows))
        await db.commit()
        return created


async def deliveries(notification_ids, provider=None):
    async with SessionLocal() as db:
        query = select(NotificationDelivery).where(
            NotificationDelivery.notification_id.in_(notification_ids),
            NotificationDelivery.provider_id != INBOX_PROVIDER,
        )
        if provider:
            query = query.where(NotificationDelivery.provider_id == provider)
        return list(await db.scalars(query))


async def release(db):
    """Close every window and make the resulting work due, as the wall clock would."""
    sealed = await seal_due_windows(db, now=int(time.time()) + FAR)
    await db.execute(
        update(NotificationDelivery)
        .where(NotificationDelivery.status == "pending")
        .values(next_attempt_at=0)
    )
    await db.commit()
    return sealed


@pytest.fixture
async def transport(account, monkeypatch):
    fake = Transport()
    monkeypatch.setattr(PROVIDERS, AsyncMock(return_value={"a": fake}))
    async with SessionLocal() as db:
        await add_destination(db, account, "a")
        await db.commit()
    return fake


async def drain(db, rounds=10):
    sent = 0
    for _ in range(rounds):
        sent += await process_pending_deliveries(db)
    return sent


async def test_related_events_are_held_in_one_window_while_the_inbox_stays_immediate(
    account, transport
):
    show = uuid4()
    ids = await emit(account, *(media_row(media_id=show, title=f"Ep {n}") for n in range(3)))
    other = await emit(account, media_row())
    held = await deliveries(ids)
    assert {work.status for work in held} == {"aggregating"}
    assert len({work.window_id for work in held}) == 1
    assert (await deliveries(other))[0].window_id not in {work.window_id for work in held}
    async with SessionLocal() as db:
        inbox = await db.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id.in_(ids),
                NotificationDelivery.provider_id == INBOX_PROVIDER,
            )
        )
        assert {work.status for work in inbox} == {"sent"}
        assert all(
            note.inbox_visible
            for note in await db.scalars(select(Notification).where(Notification.id.in_(ids)))
        )
        window = await db.get(NotificationAggregationWindow, held[0].window_id)
        assert window.member_count == 3 and window.status == "open"
        assert window.window_end - window.window_start == 900


async def test_replayed_event_is_deduplicated_but_distinct_related_events_are_not(
    account, transport
):
    show = uuid4()
    row = media_row(media_id=show)
    first = await emit(account, row)
    assert await emit(account, row) == []
    second = await emit(account, media_row(media_id=show, title="Different"))
    work = await deliveries(first + second)
    assert len(work) == 2 and len({entry.window_id for entry in work}) == 1
    async with SessionLocal() as db:
        assert (await db.get(NotificationAggregationWindow, work[0].window_id)).member_count == 2


async def test_fake_clock_window_boundaries_are_exact(account, transport):
    ids = await emit(account, media_row())
    async with SessionLocal() as db:
        window = await db.get(NotificationAggregationWindow, (await deliveries(ids))[0].window_id)
        end = window.window_end
        assert await seal_due_windows(db, now=end - 1) == 0
        assert await process_pending_deliveries(db) == 0
        assert await seal_due_windows(db, now=end) == 1
        assert await seal_due_windows(db, now=end + 1000) == 0
        await db.refresh(window)
        assert window.status == "sealed" and window.sealed_at == end
    [work] = await deliveries(ids)
    assert work.status == "pending" and work.next_attempt_at == end


async def test_sealed_window_sends_one_digest_with_item_identity_and_settles_members(
    account, transport
):
    show = uuid4()
    ids = await emit(account, *(media_row(media_id=show, title=f"Ep {n}") for n in range(3)))
    async with SessionLocal() as db:
        assert await release(db) == 1
        assert [w.status for w in await deliveries(ids)].count("pending") == 1
        assert await drain(db) == 1
    [message] = transport.messages
    assert message.title == "3 new episodes"
    assert {entry.id for entry in message.items} == set(ids)
    assert [entry.event_at for entry in message.items] == sorted(
        entry.event_at for entry in message.items
    )
    assert all(entry.media_id == show for entry in message.items)
    assert message.body.count("\n- ") == 2 and "Ep 0" in message.body
    assert {work.status for work in await deliveries(ids)} == {"sent"}


async def test_single_member_window_sends_the_ordinary_message(account, transport):
    [identity] = await emit(account, media_row(title="Solo"))
    async with SessionLocal() as db:
        await release(db)
        assert await drain(db) == 1
    [message] = transport.messages
    assert message.title == "Solo" and message.items == () and message.id == identity


async def test_overlapping_workers_seal_a_window_exactly_once(account, transport):
    ids = await emit(account, *(media_row(media_id=uuid4()) for _ in range(1)))
    ids += await emit(account, *(media_row(kind="game_sale") for _ in range(4)))

    async def worker():
        async with SessionLocal() as db:
            return await seal_due_windows(db, now=int(time.time()) + FAR)

    results = await asyncio.gather(worker(), worker(), worker())
    assert sum(results) == 2
    work = await deliveries(ids)
    assert len({w.batch_id for w in work}) == 2
    assert sorted(w.status for w in work).count("pending") == 2


async def test_overlapping_dispatchers_deliver_each_batch_once(account, transport):
    show = uuid4()
    ids = await emit(account, *(media_row(media_id=show) for _ in range(4)))
    async with SessionLocal() as db:
        await release(db)

    async def worker():
        async with SessionLocal() as db:
            return await drain(db, 3)

    assert sum(await asyncio.gather(worker(), worker())) == 1
    assert len(transport.messages) == 1
    assert {w.status for w in await deliveries(ids)} == {"sent"}


async def test_restart_reclaims_an_expired_lease_without_losing_or_doubling_the_batch(
    account, transport
):
    show = uuid4()
    ids = await emit(account, *(media_row(media_id=show) for _ in range(3)))
    async with SessionLocal() as db:
        await release(db)
        # A worker leased the lead and died before sending.
        await db.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.status == "pending")
            .values(status="processing", claim_token=uuid4(), lease_until=1)
        )
        await db.commit()
    # A restarted process holds no state; the persisted window and lease are enough.
    async with SessionLocal() as db:
        assert await seal_due_windows(db, now=int(time.time()) + FAR) == 0
        assert await drain(db) == 1
    assert len(transport.messages) == 1 and len(transport.messages[0].items) == 3
    assert {w.status for w in await deliveries(ids)} == {"sent"}


async def test_failed_chunk_retries_as_a_unit_then_fails_every_member(account, transport):
    transport.outcomes = [DeliveryResult(success=False, retryable=True)] * 3
    show = uuid4()
    ids = await emit(account, *(media_row(media_id=show) for _ in range(3)))
    async with SessionLocal() as db:
        await release(db)
        await drain(db, 1)
        statuses = sorted(w.status for w in await deliveries(ids))
        assert statuses == ["aggregating", "aggregating", "retry_wait"]
        for _ in range(2):
            await db.execute(update(NotificationDelivery).values(next_attempt_at=0))
            await db.commit()
            await drain(db, 1)
    assert len(transport.messages) == 3
    assert {w.status for w in await deliveries(ids)} == {"failed_permanent"}


async def test_transient_failure_followed_by_success_sends_each_member_once(account, transport):
    transport.outcomes = [DeliveryResult(success=False, retryable=True)]
    ids = await emit(account, *(media_row(media_id=UUID(int=9)) for _ in range(3)))
    async with SessionLocal() as db:
        await release(db)
        await drain(db, 1)
        await db.execute(update(NotificationDelivery).values(next_attempt_at=0))
        await db.commit()
        assert await drain(db) == 1
    assert len(transport.messages) == 2
    assert {w.status for w in await deliveries(ids)} == {"sent"}
    async with SessionLocal() as db:
        assert await drain(db) == 0
    assert len(transport.messages) == 2


async def test_large_windows_split_deterministically_and_every_part_is_delivered(
    account, transport, monkeypatch
):
    monkeypatch.setattr(policy, "MAX_CHUNK_ITEMS", 2)
    show = uuid4()
    base = int(time.time())
    ids = await emit(
        account,
        *(media_row(media_id=show, title=f"Ep {n}", event_at=base + n) for n in range(5)),
    )
    async with SessionLocal() as db:
        await release(db)
        work = await deliveries(ids)
        assert sorted({w.batch_index for w in work}) == [0, 1, 2]
        assert len({w.batch_id for w in work}) == 3
        assert await drain(db) == 3
    # The last part holds a single item and uses the ordinary message.
    assert sorted(len(message.items) for message in transport.messages) == [0, 2, 2]
    assert sorted(message.title for message in transport.messages)[:2] == [
        "2 new episodes (1/3)",
        "2 new episodes (2/3)",
    ]
    seen = [entry.id for message in transport.messages for entry in message.items]
    assert len(seen) == 4 and set(seen) <= set(ids)
    assert {w.status for w in await deliveries(ids)} == {"sent"}


async def test_mixed_providers_chunk_independently_and_stay_isolated(account, monkeypatch):
    good, bad = Transport(), Transport([DeliveryResult(success=False, retryable=False)])
    monkeypatch.setattr(PROVIDERS, AsyncMock(return_value={"a": good, "b": bad}))
    async with SessionLocal() as db:
        await add_destination(db, account, "a")
        await add_destination(db, account, "b")
        await db.commit()
    show = uuid4()
    ids = await emit(account, *(media_row(media_id=show) for _ in range(3)))
    async with SessionLocal() as db:
        await release(db)
        await drain(db)
    assert len(good.messages) == 1 and len(bad.messages) == 1
    assert {w.status for w in await deliveries(ids, "a")} == {"sent"}
    # A permanent failure ends the whole chunk; nothing is left held or retried forever.
    assert {w.status for w in await deliveries(ids, "b")} == {"failed_permanent"}
    assert len({w.window_id for w in await deliveries(ids)}) == 2


async def test_deleted_member_is_cancelled_and_deleted_lead_hands_over(account, transport):
    show = uuid4()
    ids = await emit(account, *(media_row(media_id=show) for _ in range(3)))
    async with SessionLocal() as db:
        await release(db)
        lead = next(w for w in await deliveries(ids) if w.status == "pending")
        assert await delete_notice(db, account, lead.notification_id)
        await db.commit()
        # The bulk cancellation left a headless chunk; upkeep promotes the next member.
        assert await recover_orphans(db) == 1
        assert await drain(db) == 1
    [message] = transport.messages
    assert lead.notification_id not in {entry.id for entry in message.items}
    assert len(message.items) == 2
    states = {w.notification_id: w.status for w in await deliveries(ids)}
    assert states[lead.notification_id] == "cancelled"
    assert list(states.values()).count("sent") == 2


async def test_member_deleted_after_sealing_is_left_out_of_the_digest(account, transport):
    show = uuid4()
    ids = await emit(account, *(media_row(media_id=show) for _ in range(3)))
    async with SessionLocal() as db:
        await release(db)
        member = next(w for w in await deliveries(ids) if w.status == "aggregating")
        await db.execute(
            update(Notification)
            .where(Notification.id == member.notification_id)
            .values(deleted_at=int(time.time()))
        )
        await db.commit()
        assert await drain(db) == 1
    assert member.notification_id not in {entry.id for entry in transport.messages[0].items}
    states = {w.notification_id: w.status for w in await deliveries(ids)}
    assert states[member.notification_id] == "cancelled"


async def test_empty_and_fully_cancelled_windows_seal_without_work(account, transport):
    ids = await emit(account, media_row())
    async with SessionLocal() as db:
        await delete_notice(db, account, ids[0])
        await db.commit()
        assert await release(db) == 1
        assert await drain(db) == 0
    assert transport.messages == []


async def test_urgent_route_bypasses_aggregation_when_policy_permits(account, transport):
    from src.core.preferences import save_preferences

    async with SessionLocal() as db:
        destination = await db.scalar(
            select(NotificationDestination).where(
                NotificationDestination.user_id == account,
                NotificationDestination.provider_id == "a",
            )
        )
        await save_preferences(
            db,
            account,
            {
                "notification_routes": {
                    "media.movie.released": {
                        str(destination.id): {"enabled": True, "urgency": "critical"}
                    }
                }
            },
        )
    urgent = await emit(account, media_row("movie_released"))
    ordinary = await emit(account, media_row("episode_aired"))
    assert (await deliveries(urgent))[0].status == "pending"
    assert (await deliveries(urgent))[0].window_id is None
    assert (await deliveries(ordinary))[0].status == "aggregating"


@pytest.mark.parametrize("kind", ["session_anomaly", "game_price_hit"])
async def test_security_and_price_alerts_are_never_delayed(account, transport, kind):
    ids = await emit(account, media_row(kind))
    assert (await deliveries(ids))[0].status == "pending"


async def test_game_sales_and_plugin_updates_aggregate_app_wide(account, transport):
    sales = await emit(account, *(media_row("game_sale") for _ in range(3)))
    updates = await emit(
        account,
        *(
            media_row(
                "plugin_update",
                story=StoryReference(
                    key=f"update:plugin-{n}:2.0",
                    phase="available",
                    lifecycle=PLUGIN_UPDATE_LIFECYCLE,
                ),
            )
            for n in range(3)
        ),
    )
    sale_work, update_work = await deliveries(sales), await deliveries(updates)
    assert len({w.window_id for w in sale_work}) == 1
    assert len({w.window_id for w in update_work}) == 1
    async with SessionLocal() as db:
        window = await db.get(NotificationAggregationWindow, update_work[0].window_id)
        assert window.strategy == "digest" and window.window_end - window.window_start == 86400
        assert window.window_start % 86400 == 0
        await release(db)
        await drain(db)
    titles = sorted(message.title for message in transport.messages)
    assert titles == ["3 game sales", "3 plugin updates"]


async def test_newer_story_revision_supersedes_older_one_in_the_same_window(account, transport):
    def revision(phase, dedupe):
        return media_row(
            "plugin_update",
            dedupe_key=dedupe,
            story=StoryReference(
                key="update:example:2.0", phase=phase, lifecycle=PLUGIN_UPDATE_LIFECYCLE
            ),
        )

    first = await emit(account, revision("available", "u-1"))
    async with SessionLocal() as db:
        # Acknowledged content is never edited, so the next revision becomes a linked item.
        await db.execute(update(Notification).where(Notification.id == first[0]).values(read_at=1))
        await db.commit()
    second = await emit(account, revision("failed", "u-2"))
    assert first and second and first != second
    async with SessionLocal() as db:
        await release(db)
        await drain(db)
    states = {w.notification_id: w.status for w in await deliveries(first + second)}
    assert states[first[0]] == "suppressed" and states[second[0]] == "sent"
    assert len(transport.messages) == 1


async def test_window_member_cap_closes_it_early_and_opens_a_new_one(
    account, transport, monkeypatch
):
    from src.features import notification_aggregation as module

    monkeypatch.setattr(module, "MAX_WINDOW_MEMBERS", 2)
    show = uuid4()
    ids = await emit(account, *(media_row(media_id=show) for _ in range(5)))
    work = await deliveries(ids)
    assert sorted(
        [sum(1 for w in work if w.window_id == window) for window in {w.window_id for w in work}]
    ) == [1, 2, 2]
    async with SessionLocal() as db:
        full = await db.scalar(
            select(func.count())
            .select_from(NotificationAggregationWindow)
            .where(NotificationAggregationWindow.status == "full")
        )
        assert full >= 2
        await release(db)
        assert await drain(db) == 3


async def test_destination_revision_change_suppresses_held_work(account, transport):
    ids = await emit(account, *(media_row() for _ in range(2)))
    async with SessionLocal() as db:
        await db.execute(
            update(NotificationDelivery)
            .where(NotificationDelivery.notification_id.in_(ids))
            .values(destination_revision=99)
        )
        await db.commit()
        await release(db)
        assert await drain(db) == 0
    assert transport.messages == []
    assert {w.status for w in await deliveries(ids)} <= {"suppressed", "aggregating"}


async def test_sealed_windows_are_pruned_only_after_retention_and_when_idle(account, transport):
    ids = await emit(account, media_row())
    async with SessionLocal() as db:
        await release(db)
        await drain(db)
        window_id = (await deliveries(ids))[0].window_id
        assert await purge_sealed(db, now=int(time.time())) == 0
        later = int(time.time()) + FAR + policy.SEALED_WINDOW_RETENTION_SECONDS + 10
        assert await purge_sealed(db, now=later) >= 1
        assert await db.get(NotificationAggregationWindow, window_id) is None
    assert {w.status for w in await deliveries(ids)} == {"sent"}
