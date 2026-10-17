"""Capability-aware dispatch: batches, edits, follow-ups, references and per-send rechecks."""

import time
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update

from src.core.config import settings
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_delivery_attempt import NotificationDeliveryAttempt
from src.database.models.notification_destination import NotificationDestination
from src.database.models.notification_provider_setting import NotificationProviderSetting
from src.database.models.notification_remote_reference import NotificationRemoteReference
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import notification_aggregation_policy as policy
from src.features.notification_aggregation import seal_due_windows
from src.features.notification_controller import emit_legacy_rows
from src.features.notification_lifecycle import delete_notice
from src.features.notification_policy import INBOX_PROVIDER
from src.features.notification_providers import delivery as dispatcher
from src.features.notification_providers.base import DeliveryResult, ProviderDestination
from src.features.notification_providers.capabilities import resolve_profile
from src.features.notification_providers.delivery import (
    ensure_deliveries,
    process_pending_deliveries,
)
from src.features.notification_reconcile import story_delivery_diagnostics
from src.features.notification_stories import PLUGIN_UPDATE_LIFECYCLE, StoryReference
from src.plugin_api.notification_story_contracts import (
    NOTIFICATION_CONTRACT_VERSION,
    ImageCapabilities,
    NotificationProviderCapabilities,
)

PROVIDERS = "src.features.notification_providers.delivery.get_notification_providers"
REGISTRY = "src.features.notification_providers.registry.get_notification_providers"
FAR = 10 * 365 * 86400


def declared(**changes) -> NotificationProviderCapabilities:
    values = {"contract_version": NOTIFICATION_CONTRACT_VERSION, "links": True, **changes}
    return NotificationProviderCapabilities(**values)


class Remote:
    """In-process adapter recording every physical operation it receives."""

    transport = None
    name = "Remote"

    def __init__(self, provider_id="rich", capabilities=None, can_edit=True):
        self.id = provider_id
        self.capabilities = capabilities
        self.calls: list[tuple[str, object]] = []
        self.sends: list[DeliveryResult] = []
        self.edits: list[DeliveryResult] = []
        self.counter = 0
        if not can_edit:
            self.edit = None

    async def lookup_destination(self, _db, user, _setting):
        return ProviderDestination(user.id, self.name)

    async def deliver(self, _db, _target, message):
        self.calls.append((message.operation, message))
        if self.sends:
            return self.sends.pop(0)
        self.counter += 1
        return DeliveryResult(success=True, remote_ref=f"{self.id}-{self.counter}")

    async def edit(self, _db, _target, message):
        self.calls.append((message.operation, message))
        if self.edits:
            return self.edits.pop(0)
        return DeliveryResult(success=True, remote_ref=message.remote_ref)


@pytest.fixture
async def account():
    user_id = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                username=f"cap_{user_id.hex}",
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


async def add_destination(account, provider) -> UUID:
    async with SessionLocal() as db:
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
            notification_url="https://app.example.test",
            verification_method="test.host.proof",
        )
        db.add(target)
        db.add(NotificationProviderSetting(user_id=account, provider_id=provider, enabled=True))
        await db.commit()
        return target.id


def install(monkeypatch, *remotes, windows=False):
    mapping = {remote.id: remote for remote in remotes}
    monkeypatch.setattr(PROVIDERS, AsyncMock(return_value=mapping))
    monkeypatch.setattr(REGISTRY, AsyncMock(return_value=mapping))
    if not windows:
        monkeypatch.setattr(dispatcher, "assign_window", AsyncMock(return_value=None))


def reference(phase, key="update:example:2.0.0"):
    return StoryReference(key=key, phase=phase, lifecycle=PLUGIN_UPDATE_LIFECYCLE)


def row(phase, key="update:example:2.0.0", title=None, **changes):
    return {
        "kind": "plugin_update",
        "media_type": "plugin",
        "media_id": uuid4(),
        "title": title or f"Update {phase}",
        "body": f"Version 2.0.0 {phase}",
        "poster_url": None,
        "event_at": int(time.time()),
        "dedupe_key": f"{key}:{phase}",
        "story": reference(phase, key),
        **changes,
    }


async def accept(account, *rows):
    async with SessionLocal() as db:
        created = await emit_legacy_rows(db, account, list(rows))
        await db.commit()
        return created


async def drain(rounds=6):
    total = 0
    for _ in range(rounds):
        async with SessionLocal() as db:
            await db.execute(
                update(NotificationDelivery)
                .where(NotificationDelivery.status.in_(("pending", "retry_wait")))
                .values(next_attempt_at=0)
            )
            await db.commit()
            total += await process_pending_deliveries(db)
    return total


async def external(account):
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(NotificationDelivery)
                .join(Notification, Notification.id == NotificationDelivery.notification_id)
                .where(
                    Notification.user_id == account,
                    NotificationDelivery.provider_id != INBOX_PROVIDER,
                )
                .order_by(NotificationDelivery.story_revision, NotificationDelivery.provider_id)
            )
        )


async def references(account):
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(NotificationRemoteReference)
                .join(Notification, Notification.id == NotificationRemoteReference.notification_id)
                .where(Notification.user_id == account)
            )
        )


# Capability contract --------------------------------------------------------------------------


def test_profiles_come_from_the_adapter_and_never_exceed_what_it_implements():
    plain = resolve_profile(Remote(capabilities=None))
    assert not plain.canonical and not plain.can_edit and plain.capabilities.max_batch_items == 1
    rich = resolve_profile(Remote(capabilities=declared(message_updates="edit", max_batch_items=5)))
    assert rich.canonical and rich.can_edit and rich.capabilities.max_batch_items == 5
    # A declaration the adapter cannot back is narrowed and the downgrade is visible.
    narrowed = resolve_profile(
        Remote(capabilities=declared(message_updates="edit"), can_edit=False)
    )
    assert not narrowed.can_edit and "edit_not_implemented" in narrowed.notes
    # Built-in and legacy-transport adapters keep their fixed profiles.
    legacy = Remote("core.smtp", capabilities=declared(message_updates="edit"))
    assert resolve_profile(legacy).capabilities.message_updates == "none"
    assert "declaration_ignored" in resolve_profile(legacy).notes
    transport = Remote(capabilities=declared(message_updates="edit"))
    transport.transport = "legacy"
    assert not resolve_profile(transport).canonical
    assert resolve_profile(object()).capabilities.max_batch_items == 1


# Reconciliation -------------------------------------------------------------------------------


async def test_later_revision_edits_the_remote_message_once(account, monkeypatch):
    remote = Remote(capabilities=declared(message_updates="edit"))
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    [first] = await accept(account, row("available"))
    assert await drain() == 1
    [saved] = await references(account)
    assert (saved.remote_ref, saved.delivered_revision, saved.state) == ("rich-1", 1, "current")
    # The same story moves on: the existing message is edited, not repeated.
    assert await accept(account, row("failed", title="Update failed")) == [first]
    assert await drain() == 1
    assert [name for name, _ in remote.calls] == ["send", "edit"]
    edit = remote.calls[1][1]
    assert edit.remote_ref == "rich-1" and edit.revision == 2 and edit.title == "Update failed"
    [saved] = await references(account)
    assert (saved.remote_ref, saved.delivered_revision) == ("rich-1", 2)
    work = await external(account)
    assert [(w.story_revision, w.operation, w.status) for w in work] == [
        (1, "send", "sent"),
        (2, "edit", "sent"),
    ]
    # Nothing is owed again: a replay, and another reconcile pass, add no work.
    assert await accept(account, row("failed", title="Update failed")) == []
    async with SessionLocal() as db:
        await ensure_deliveries(db, [first])
        await db.commit()
    assert await drain() == 0 and len(await external(account)) == 2
    assert len(remote.calls) == 2


async def test_unchanged_presentation_is_not_edited_again(account, monkeypatch):
    remote = Remote(capabilities=declared(message_updates="edit"))
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    await accept(account, row("available", title="Same title", body="Same body"))
    await drain()
    await accept(account, row("failed", title="Same title", body="Same body"))
    await drain()
    # The phase changed but the reader would see the identical message: no remote call at all.
    assert [name for name, _ in remote.calls] == ["send"]
    second = (await external(account))[1]
    assert second.status == "sent" and "unchanged" in second.fallback
    [saved] = await references(account)
    assert saved.delivered_revision == 2 and saved.remote_ref == "rich-1"


async def test_unsupported_edit_uses_the_follow_up_rule_and_says_so(account, monkeypatch):
    remote = Remote()  # legacy profile: no edits
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    monkeypatch.setattr(settings, "NOTIFICATION_FOLLOW_UPS", "important")
    await accept(account, row("available"))
    await drain()
    # An error-severity revision is worth a follow-up, and the old message is not edited.
    await accept(account, row("failed"))
    await drain()
    assert [name for name, _ in remote.calls] == ["send", "follow_up"]
    assert remote.calls[1][1].title == "Update: Update failed"
    work = await external(account)
    assert work[1].operation == "follow_up" and "edit_unsupported" in work[1].fallback
    # An informational revision is dropped by the rule, visibly, without any remote call.
    await accept(account, row("installed"))
    await drain()
    last = (await external(account))[2]
    assert (last.status, last.last_error) == ("suppressed", "follow_up_policy")
    assert len(remote.calls) == 2


@pytest.mark.parametrize(("mode", "expected"), [("all", "follow_up"), ("none", "revision_policy")])
async def test_follow_up_rule_is_configurable(account, monkeypatch, mode, expected):
    remote = Remote()
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    monkeypatch.setattr(settings, "NOTIFICATION_FOLLOW_UPS", mode)
    await accept(account, row("available"))
    await drain()
    await accept(account, row("installed"))
    await drain()
    second = (await external(account))[1]
    if mode == "all":
        assert second.operation == expected and second.status == "sent"
    else:
        assert second.last_error == expected and second.status == "suppressed"


async def test_refused_edit_falls_back_to_a_follow_up_and_marks_the_reference(account, monkeypatch):
    remote = Remote(capabilities=declared(message_updates="edit", threaded_follow_ups=True))
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    await accept(account, row("available"))
    await drain()
    remote.edits.append(DeliveryResult(success=False, error="gone", gone=True))
    await accept(account, row("failed"))
    await drain()
    assert [name for name, _ in remote.calls] == ["send", "edit", "follow_up"]
    # The follow-up threads under the message that could not be edited.
    assert remote.calls[2][1].remote_ref == "rich-1"
    second = (await external(account))[1]
    assert (second.status, second.operation, second.attempts) == ("sent", "follow_up", 2)
    assert "edit_failed" in second.fallback
    async with SessionLocal() as db:
        attempts = list(
            await db.scalars(
                select(NotificationDeliveryAttempt)
                .where(NotificationDeliveryAttempt.delivery_id == second.id)
                .order_by(NotificationDeliveryAttempt.number)
            )
        )
    assert [(a.operation, a.outcome) for a in attempts] == [
        ("edit", "failed"),
        ("follow_up", "sent"),
    ]
    [saved] = await references(account)
    assert (saved.remote_ref, saved.state) == ("rich-2", "current")


async def test_retryable_edit_failure_retries_the_edit(account, monkeypatch):
    remote = Remote(capabilities=declared(message_updates="edit"))
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    await accept(account, row("available"))
    await drain()
    remote.edits.append(DeliveryResult(success=False, retryable=True, error="busy"))
    await accept(account, row("failed"))
    await drain()
    assert [name for name, _ in remote.calls] == ["send", "edit", "edit"]
    # Both edit attempts carry the same idempotency key.
    assert remote.calls[1][1].idempotency_key == remote.calls[2][1].idempotency_key
    assert (await external(account))[1].attempts == 2


async def test_shared_digest_message_is_never_edited(account, monkeypatch):
    remote = Remote(capabilities=declared(message_updates="edit", max_batch_items=10))
    install(monkeypatch, remote, windows=True)
    await add_destination(account, remote.id)
    first, second = await accept(
        account, row("available", key="update:a:1"), row("available", key="update:b:1")
    )
    async with SessionLocal() as db:
        await seal_due_windows(db, now=int(time.time()) + FAR)
    await drain()
    assert len(remote.calls) == 1 and len(remote.calls[0][1].items) == 2
    assert {ref.shared for ref in await references(account)} == {True}
    await accept(account, row("failed", key="update:a:1"))
    await drain()
    # The digest also presented another item, so the change is a follow-up (if the rule allows).
    assert remote.calls[-1][0] == "follow_up"
    assert "shared_message" in (await external(account))[-1].fallback
    assert first != second


async def test_reference_is_scoped_to_the_destination_revision(account, monkeypatch):
    remote = Remote(capabilities=declared(message_updates="edit"))
    install(monkeypatch, remote)
    destination = await add_destination(account, remote.id)
    await accept(account, row("available"))
    await drain()
    async with SessionLocal() as db:
        await db.execute(
            update(NotificationDestination)
            .where(NotificationDestination.id == destination)
            .values(revision=2, verified_revision=2)
        )
        await db.commit()
    await accept(account, row("failed"))
    await drain()
    # A new destination revision never saw the first message, so it gets a fresh send.
    assert [name for name, _ in remote.calls] == ["send", "send"]


# Isolation, retry and crash behaviour -----------------------------------------------------------


async def test_a_failed_destination_does_not_block_another(account, monkeypatch):
    failing = Remote("bad", capabilities=declared(message_updates="edit"))
    working = Remote("good", capabilities=declared(message_updates="edit"))
    install(monkeypatch, failing, working)
    await add_destination(account, "bad")
    await add_destination(account, "good")
    failing.sends = [DeliveryResult(success=False, error="down")] * 5
    await accept(account, row("available"))
    await drain()
    by_provider = {w.provider_id: w for w in await external(account)}
    assert by_provider["good"].status == "sent"
    assert by_provider["bad"].status == "failed_permanent"
    assert {ref.provider_id for ref in await references(account)} == {"good"}
    # The healthy destination keeps editing; the failed one has nothing to edit.
    await accept(account, row("failed"))
    await drain()
    assert [name for name, _ in working.calls] == ["send", "edit"]
    assert [name for name, _ in failing.calls].count("edit") == 0


async def test_crash_after_send_resends_with_the_same_idempotency_key(account, monkeypatch):
    remote = Remote()
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    await accept(account, row("available"))

    async def crash(_db, _target, message):
        remote.calls.append((message.operation, message))
        raise KeyboardInterrupt  # the worker dies after the provider accepted the message

    original = remote.deliver
    remote.deliver = crash
    async with SessionLocal() as db:
        await db.execute(update(NotificationDelivery).values(next_attempt_at=0))
        await db.commit()
        with pytest.raises(KeyboardInterrupt):
            await process_pending_deliveries(db)
    [stuck] = await external(account)
    assert stuck.status == "processing" and await references(account) == []
    remote.deliver = original
    async with SessionLocal() as db:
        await db.execute(update(NotificationDelivery).values(lease_until=int(time.time()) - 1))
        await db.commit()
    await drain()
    [done] = await external(account)
    assert done.status == "sent" and done.attempts == 2
    # At least once, honestly: two physical sends, one stable key a provider can deduplicate on.
    assert len(remote.calls) == 2
    assert remote.calls[0][1].idempotency_key == remote.calls[1][1].idempotency_key
    async with SessionLocal() as db:
        outcomes = list(
            await db.scalars(
                select(NotificationDeliveryAttempt.outcome)
                .where(NotificationDeliveryAttempt.delivery_id == done.id)
                .order_by(NotificationDeliveryAttempt.number)
            )
        )
    assert outcomes == ["unknown", "sent"]


async def test_crash_after_edit_repeats_the_edit_safely(account, monkeypatch):
    remote = Remote(capabilities=declared(message_updates="edit"))
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    await accept(account, row("available"))
    await drain()
    await accept(account, row("failed"))
    original = remote.edit

    async def crash(_db, _target, message):
        remote.calls.append((message.operation, message))
        raise KeyboardInterrupt

    remote.edit = crash
    async with SessionLocal() as db:
        await db.execute(update(NotificationDelivery).values(next_attempt_at=0))
        await db.commit()
        with pytest.raises(KeyboardInterrupt):
            await process_pending_deliveries(db)
    remote.edit = original
    async with SessionLocal() as db:
        await db.execute(update(NotificationDelivery).values(lease_until=int(time.time()) - 1))
        await db.commit()
    await drain()
    assert [name for name, _ in remote.calls] == ["send", "edit", "edit"]
    [saved] = await references(account)
    assert (saved.remote_ref, saved.delivered_revision) == ("rich-1", 2)


@pytest.mark.parametrize("race", ["deleted", "rerouted"])
async def test_revocation_between_lease_and_send_stops_the_send(account, monkeypatch, race):
    remote = Remote()
    install(monkeypatch, remote)
    destination = await add_destination(account, remote.id)
    [notice] = await accept(account, row("available"))
    real = dispatcher.invitation_message

    async def revoke(db, notification, endpoint, message):
        async with SessionLocal() as other:
            if race == "deleted":
                await delete_notice(other, account, notice)
            else:
                await other.execute(
                    update(NotificationDestination)
                    .where(NotificationDestination.id == destination)
                    .values(revision=2)
                )
            await other.commit()
        return await real(db, notification, endpoint, message)

    monkeypatch.setattr(dispatcher, "invitation_message", revoke)
    await drain()
    assert remote.calls == []
    [work] = await external(account)
    assert work.status in {"cancelled", "suppressed"}
    async with SessionLocal() as db:
        attempts = list(
            await db.scalars(
                select(NotificationDeliveryAttempt).where(
                    NotificationDeliveryAttempt.delivery_id == work.id
                )
            )
        )
    assert all(a.outcome in {"skipped", "processing"} for a in attempts)
    assert await references(account) == []


async def test_deleting_a_notice_drops_its_remote_references(account, monkeypatch):
    remote = Remote()
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    [notice] = await accept(account, row("available"))
    await drain()
    assert len(await references(account)) == 1
    async with SessionLocal() as db:
        await delete_notice(db, account, notice)
        await db.commit()
    assert await references(account) == []


# Batches --------------------------------------------------------------------------------------


async def test_batches_respect_the_declared_item_and_text_limits_and_keep_links(
    account, monkeypatch
):
    caps = declared(max_batch_items=3, max_text_chars=2000)
    remote = Remote(capabilities=caps)
    install(monkeypatch, remote, windows=True)
    await add_destination(account, remote.id)
    ids = await accept(
        account, *(row("available", key=f"update:p{n}:1", title=f"Plugin {n}") for n in range(7))
    )
    async with SessionLocal() as db:
        await seal_due_windows(db, now=int(time.time()) + FAR)
    await drain(rounds=10)
    sizes = sorted(len(message.items) for _, message in remote.calls)
    # The single leftover item is sent as an ordinary message (no digest items).
    assert sizes == [0, 3, 3] and len(ids) == 7
    for _, message in remote.calls:
        assert len(message.body) <= 2000
        for entry in message.items:
            assert entry.url and "/notifications/stories/" in entry.url
            assert entry.url in message.body
    assert {w.status for w in await external(account)} == {"sent"}


async def test_a_provider_that_cannot_batch_gets_individual_messages(account, monkeypatch):
    remote = Remote(capabilities=declared(max_batch_items=1))
    install(monkeypatch, remote, windows=True)
    await add_destination(account, remote.id)
    await accept(account, *(row("available", key=f"update:q{n}:1") for n in range(3)))
    async with SessionLocal() as db:
        await seal_due_windows(db, now=int(time.time()) + FAR)
    await drain(rounds=8)
    assert len(remote.calls) == 3 and all(not m.items for _, m in remote.calls)


def test_legacy_adapters_keep_the_host_composed_digest_limits():
    limits = policy.provider_limits("unknown", None, profile=resolve_profile(Remote()))
    assert limits.max_items == policy.MAX_CHUNK_ITEMS
    canonical = policy.provider_limits(
        "x", None, profile=resolve_profile(Remote(capabilities=declared(max_batch_items=4)))
    )
    assert canonical.max_items == 4 and canonical.links


async def test_one_chunk_failing_does_not_fail_the_other_chunks(account, monkeypatch):
    remote = Remote(capabilities=declared(max_batch_items=2))
    install(monkeypatch, remote, windows=True)
    await add_destination(account, remote.id)
    await accept(
        account,
        *(row("available", key=f"update:r{n}:1", title=f"Item {n}") for n in range(4)),
    )

    async def reject_the_chunk_with_item_zero(_db, _target, message):
        remote.calls.append((message.operation, message))
        ok = "Item 0" not in message.body
        return DeliveryResult(success=ok, error=None if ok else "rejected")

    remote.deliver = reject_the_chunk_with_item_zero
    async with SessionLocal() as db:
        await seal_due_windows(db, now=int(time.time()) + FAR)
    await drain(rounds=8)
    states = sorted(w.status for w in await external(account))
    assert states == ["failed_permanent"] * 2 + ["sent"] * 2


# Rendering ------------------------------------------------------------------------------------


async def test_rendering_follows_the_declared_text_formats_and_limits(account, monkeypatch):
    remote = Remote(
        capabilities=declared(
            text_formats=("plain", "html_email"),
            max_title_chars=20,
            max_text_chars=64,
            action_buttons=1,
            images=ImageCapabilities(max_images=1, external_urls=True),
        )
    )
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    await accept(account, row("available", title="A long title that does not fit the limit"))
    await drain()
    [(_, message)] = remote.calls
    assert len(message.title) <= 20 and message.title.endswith("…")
    assert message.html is not None and "<" in message.html
    work = (await external(account))[0]
    assert "truncated" in (work.fallback or "")


async def test_legacy_adapters_receive_plain_text_only(account, monkeypatch):
    remote = Remote()
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    await accept(account, row("available"))
    await drain()
    [(_, message)] = remote.calls
    assert message.html is None and message.actions == () and message.images == ()
    assert message.link is None


# Diagnostics ----------------------------------------------------------------------------------


async def test_diagnostics_show_operations_and_fallbacks_without_remote_identifiers(
    account, monkeypatch
):
    remote = Remote()
    install(monkeypatch, remote)
    await add_destination(account, remote.id)
    monkeypatch.setattr(settings, "NOTIFICATION_FOLLOW_UPS", "all")
    await accept(account, row("available"))
    await drain()
    await accept(account, row("installed"))
    await drain()
    async with SessionLocal() as db:
        story = (
            await db.scalars(select(Notification.story_id).where(Notification.user_id == account))
        ).first()
        report = await story_delivery_diagnostics(db, account, story)
        other = await story_delivery_diagnostics(db, uuid4(), story)
    assert [
        (r["revision"], r["operation"], r["status"])
        for r in report
        if r["provider_id"] == remote.id
    ] == [
        (1, "send", "sent"),
        (2, "follow_up", "sent"),
    ]
    assert "edit_unsupported" in report[-1]["fallbacks"]
    assert "rich-1" not in str(report) and other == []


def test_canonical_presentation_is_narrowed_to_the_declared_capabilities(monkeypatch):
    from src.features.notification_render import (
        _presentation,  # pylint: disable=import-outside-toplevel
    )
    from src.plugin_api.notification_story_contracts import NotificationContent  # noqa: PLC0415

    content = NotificationContent.model_validate(
        {
            "title": "Release",
            "summary": "Out **now**",
            "images": [
                {"source": {"kind": "external", "url": "https://img.example.test/a.png"}},
                {"source": {"kind": "external", "url": "https://img.example.test/b.png"}},
            ],
            "actions": [
                {"id": "a", "label": "Open", "target": {"kind": "link", "url": "https://x.test/1"}},
                {"id": "b", "label": "More", "target": {"kind": "link", "url": "https://x.test/2"}},
            ],
        }
    )
    full = resolve_profile(
        Remote(
            capabilities=declared(
                action_buttons=1,
                images=ImageCapabilities(max_images=1, external_urls=True),
                text_formats=("plain", "html_email"),
            )
        )
    )
    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MODE", "external")
    fields, notes = _presentation(
        content, full, "https://app.example.test/s", "https://app.example.test"
    )
    assert [a.label for a in fields["actions"]] == ["Open"]
    assert fields["images"] == ("https://img.example.test/a.png",)
    assert fields["html"] and set(notes) == {"actions_via_story_link", "images_dropped"}
    # Proxied images are for signed-in surfaces only: external messages drop them.
    monkeypatch.setattr(settings, "NOTIFICATION_IMAGE_MODE", "proxied")
    fields, notes = _presentation(content, full, None, None)
    assert fields["images"] == () and "actions_dropped" in notes
