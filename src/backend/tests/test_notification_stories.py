"""Idempotent event acceptance and durable story lifecycles on the notification controller."""

import asyncio
import time
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from src.api.routes.plugin_manager import catalogues
from src.database.models.notification import Notification
from src.database.models.notification_delivery import NotificationDelivery
from src.database.models.notification_receipt import NotificationReceipt
from src.database.models.notification_story import NotificationStory, NotificationStoryRevision
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notification_contract import RevisionDecision
from src.features.notification_controller import (
    NotificationDraft,
    NotificationInterpretation,
    emit_interpreted,
    emit_legacy_rows,
)
from src.features.notification_lifecycle import delete_notice
from src.features.notification_policy import Trust
from src.features.notification_source_policy import retire_sources
from src.features.notification_stories import (
    PLUGIN_UPDATE_LIFECYCLE,
    StoryReference,
    StoryTransitionError,
)
from src.plugin_api.notification_story_contracts import (
    NOTIFICATION_CONTRACT_VERSION,
    StoryLifecycleDeclaration,
    StoryPhase,
    StoryTransition,
)

PLUGIN = "example.stories"


@pytest.fixture
async def owner():
    user_id = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                username=user_id.hex,
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


def reference(phase, key="update:example:2.0.0", **changes):
    return StoryReference(key=key, phase=phase, lifecycle=PLUGIN_UPDATE_LIFECYCLE, **changes)


def row(phase, dedupe_key=None, body=None, story=None, **changes):
    return {
        "kind": "plugin_update",
        "media_type": "plugin",
        "media_id": uuid4(),
        "title": f"Update {phase}",
        "body": body or f"Version 2.0.0 {phase}",
        "poster_url": None,
        "event_at": int(time.time()),
        "dedupe_key": dedupe_key or f"plugin-update:example:2.0.0:{phase}",
        "story": story or reference(phase),
        **changes,
    }


async def accept(user_id, *rows):
    async with SessionLocal() as db:
        created = await emit_legacy_rows(db, user_id, list(rows))
        await db.commit()
        return created


async def snapshot(user_id):
    async with SessionLocal() as db:
        items = list(
            await db.scalars(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.story_revision)
            )
        )
        stories = list(
            await db.scalars(select(NotificationStory).where(NotificationStory.user_id == user_id))
        )
        receipts = (
            await db.scalar(
                select(func.count())
                .select_from(NotificationReceipt)
                .where(NotificationReceipt.user_id == user_id)
            )
            or 0
        )
        deliveries = (
            await db.scalar(
                select(func.count())
                .select_from(NotificationDelivery)
                .join(Notification, Notification.id == NotificationDelivery.notification_id)
                .where(Notification.user_id == user_id)
            )
            or 0
        )
        return items, stories, receipts, deliveries


async def mark(notification_id, **values):
    async with SessionLocal() as db:
        await db.execute(
            update(Notification).where(Notification.id == notification_id).values(**values)
        )
        await db.commit()


async def test_new_story_persists_identity_revision_and_receipt_story(owner):
    [created] = await accept(owner, row("available", "plugin-update:example:2.0.0"))
    items, stories, receipts, deliveries = await snapshot(owner)
    [item], [story] = items, stories
    assert item.id == created and item.story_id == story.id and item.story_revision == 1
    assert (story.phase, story.revision, story.scope_key, story.story_key) == (
        "available",
        1,
        "host",
        "update:example:2.0.0",
    )
    assert receipts == 1 and deliveries >= 1
    async with SessionLocal() as db:
        receipt = await db.scalar(
            select(NotificationReceipt).where(NotificationReceipt.user_id == owner)
        )
        revisions = list(await db.scalars(select(NotificationStoryRevision)))
    assert (receipt.story_key, receipt.story_phase) == ("update:example:2.0.0", "available")
    assert [r.decision for r in revisions if r.story_id == story.id] == ["create_story"]


async def test_unread_story_is_updated_in_place_without_changing_item_identity(owner):
    [first] = await accept(owner, row("available", "plugin-update:example:2.0.0"))
    before = await snapshot(owner)
    [second] = await accept(owner, row("failed", body="Could not activate"))
    items, stories, receipts, deliveries = await snapshot(owner)
    assert second == first and len(items) == 1
    [item], [story] = items, stories
    assert item.dedupe_key == "plugin-update:example:2.0.0:failed"
    assert (item.title, item.body, item.severity) == (
        "Update failed",
        "Could not activate",
        "error",
    )
    assert (
        item.story_revision == 2 and item.read_at is None and item.previous_notification_id is None
    )
    assert (story.phase, story.revision) == ("failed", 2)
    assert receipts == 2 and deliveries == before[3]
    async with SessionLocal() as db:
        history = list(
            await db.scalars(
                select(NotificationStoryRevision)
                .where(NotificationStoryRevision.story_id == story.id)
                .order_by(NotificationStoryRevision.revision)
            )
        )
    assert [(r.revision, r.phase, r.decision) for r in history] == [
        (1, "available", "create_story"),
        (2, "failed", "update_in_place"),
    ]
    assert history[0].title == "Update available" and history[1].body == "Could not activate"


@pytest.mark.parametrize(
    "state",
    [
        {"read_at": 5},
        {"dismissed_at": 5},
        {"expires_at": 1},
        {"deleted_at": 5},
    ],
)
async def test_read_dismissed_expired_or_deleted_head_gets_a_linked_historical_item(owner, state):
    [first] = await accept(owner, row("available", "plugin-update:example:2.0.0"))
    await mark(first, **state)
    [second] = await accept(owner, row("installed"))
    items, stories, _, _ = await snapshot(owner)
    by_id = {item.id: item for item in items}
    old, new = by_id[first], by_id[second]
    assert second != first and len(items) == 2
    assert new.previous_notification_id == first and new.story_revision == 2
    assert new.read_at is None and new.dismissed_at is None and new.story_id == old.story_id
    # The earlier item keeps its content, state and revision.
    assert (old.title, old.story_revision) == ("Update available", 1)
    for field, value in state.items():
        assert getattr(old, field) == value
    assert (stories[0].phase, stories[0].revision) == ("installed", 2)


async def test_marking_unread_again_makes_the_head_updatable_again(owner):
    [first] = await accept(owner, row("available", "plugin-update:example:2.0.0"))
    await mark(first, read_at=5)
    await mark(first, read_at=None)
    assert await accept(owner, row("failed")) == [first]


async def test_replayed_identical_event_creates_no_story_item_or_delivery_work(owner):
    await accept(owner, row("available", "plugin-update:example:2.0.0"))
    first = await snapshot(owner)
    for _ in range(3):
        assert await accept(owner, row("available", "plugin-update:example:2.0.0")) == []
    again = await snapshot(owner)
    assert [i.id for i in again[0]] == [i.id for i in first[0]]
    assert again[2:] == first[2:]
    assert (again[1][0].revision, again[1][0].updated_at) == (
        first[1][0].revision,
        first[1][0].updated_at,
    )


async def test_conflicting_replay_is_rejected_and_changes_nothing(owner):
    await accept(owner, row("available", "plugin-update:example:2.0.0"))
    first = await snapshot(owner)
    with pytest.raises(ValueError, match="conflicting facts"):
        await accept(owner, row("available", "plugin-update:example:2.0.0", body="Different"))
    assert (await snapshot(owner))[2:] == first[2:]


async def test_rejected_transition_consumes_no_receipt_and_leaves_the_story_intact(owner):
    await accept(owner, row("available", "plugin-update:example:2.0.0"))
    await accept(owner, row("installed"))
    before = await snapshot(owner)
    with pytest.raises(StoryTransitionError) as closed:
        await accept(owner, row("failed"))
    assert closed.value.decision == RevisionDecision.REJECT_CLOSED
    after = await snapshot(owner)
    assert after[2] == before[2] == 2 and after[1][0].revision == before[1][0].revision


async def test_non_initial_phase_cannot_start_a_story_or_leave_a_placeholder(owner):
    with pytest.raises(StoryTransitionError) as rejected:
        await accept(owner, row("installed"))
    assert rejected.value.decision == RevisionDecision.REJECT_TRANSITION
    assert await snapshot(owner) == ([], [], 0, 0)
    # The same key is still free to start correctly afterwards.
    await accept(owner, row("available", "plugin-update:example:2.0.0"))
    assert (await snapshot(owner))[2] == 1


async def test_undeclared_transition_is_rejected_with_the_receipt_rolled_back(owner):
    await accept(owner, row("available", "plugin-update:example:2.0.0"))
    await accept(owner, row("failed"))
    with pytest.raises(StoryTransitionError):
        await accept(owner, row("available", "plugin-update:example:2.0.0:again"))
    assert (await snapshot(owner))[2] == 2


async def test_event_with_unknown_phase_or_reserved_purpose_cannot_join_a_story(owner):
    with pytest.raises(StoryTransitionError):
        await accept(owner, row("nonexistent", story=reference("nonexistent")))
    async with SessionLocal() as db:
        draft = NotificationDraft(
            kind="plugin",
            user_id=owner,
            entity_type="plugin",
            entity_id=uuid4(),
            occurred_at=int(time.time()),
            dedupe_key="mismatch",
            title="t",
            body="b",
            source=PLUGIN,
            story=reference("available"),
        )
        with pytest.raises(ValueError, match="Registered notification source identity|describe"):
            await emit_interpreted(
                db,
                draft,
                NotificationInterpretation(
                    "other.type", Trust.PRIVATE, "standard", "info", uuid4()
                ),
            )
        await db.rollback()
        reserved = NotificationInterpretation(
            f"{PLUGIN}.update", Trust.PRIVATE, "recovery", "info", uuid4()
        )
        with pytest.raises(ValueError, match="standard and security"):
            await emit_interpreted(db, draft, reserved)


async def test_concurrent_identical_events_accept_exactly_once(owner):
    async def one():
        async with SessionLocal() as db:
            created = await emit_legacy_rows(
                db, owner, [row("available", "plugin-update:example:2.0.0")]
            )
            await db.commit()
            return created

    results = await asyncio.gather(*(one() for _ in range(8)))
    assert sum(1 for created in results if created) == 1
    items, stories, receipts, _ = await snapshot(owner)
    assert (len(items), len(stories), receipts) == (1, 1, 1)


async def test_concurrent_distinct_events_serialize_into_sequential_revisions(owner):
    await accept(owner, row("available", "plugin-update:example:2.0.0"))

    async def revise(index):
        async with SessionLocal() as db:
            await emit_legacy_rows(
                db,
                owner,
                [
                    row(
                        "available",
                        f"plugin-update:example:2.0.0:r{index}",
                        body=f"Revision {index}",
                    )
                ],
            )
            await db.commit()

    await asyncio.gather(*(revise(index) for index in range(6)))
    items, stories, receipts, _ = await snapshot(owner)
    assert len(items) == 1 and receipts == 7
    assert stories[0].revision == 7 == items[0].story_revision
    async with SessionLocal() as db:
        revisions = list(
            await db.scalars(
                select(NotificationStoryRevision.revision).where(
                    NotificationStoryRevision.story_id == stories[0].id
                )
            )
        )
    assert sorted(revisions) == list(range(1, 8))


async def test_concurrent_first_events_for_one_story_create_one_story(owner):
    async def start(index):
        async with SessionLocal() as db:
            await emit_legacy_rows(
                db, owner, [row("available", f"plugin-update:example:2.0.0:s{index}")]
            )
            await db.commit()

    await asyncio.gather(*(start(index) for index in range(6)))
    items, stories, receipts, _ = await snapshot(owner)
    assert (len(stories), len(items), receipts) == (1, 1, 6) and stories[0].revision == 6


async def test_concurrent_read_and_transition_never_loses_the_history(owner):
    [first] = await accept(owner, row("available", "plugin-update:example:2.0.0"))
    await asyncio.gather(mark(first, read_at=5), accept(owner, row("installed")))
    items, stories, _, _ = await snapshot(owner)
    assert stories[0].phase == "installed"
    # Either the unread head was revised before the read, or a linked item preserves history.
    assert len(items) in {1, 2} and all(item.story_id == stories[0].id for item in items)
    assert items[-1].story_revision == 2


async def test_acceptance_survives_a_restart(owner):
    from src.database.session import engine

    await accept(owner, row("available", "plugin-update:example:2.0.0"))
    await engine.dispose()  # a new process starts with no connection state
    assert await accept(owner, row("available", "plugin-update:example:2.0.0")) == []
    [item] = await accept(owner, row("failed"))
    assert item is not None
    items, stories, receipts, _ = await snapshot(owner)
    assert (len(items), stories[0].revision, receipts) == (1, 2, 2)


async def test_database_enforces_story_and_revision_uniqueness(owner):
    [first] = await accept(owner, row("available", "plugin-update:example:2.0.0"))
    _, [story], _, _ = await snapshot(owner)
    async with SessionLocal() as db:
        db.add(
            NotificationStory(
                user_id=owner,
                event_type=story.event_type,
                scope_key=story.scope_key,
                story_key=story.story_key,
                source="host",
                phase="available",
                revision=1,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()
    async with SessionLocal() as db:
        db.add(
            NotificationStoryRevision(
                story_id=story.id,
                revision=1,
                phase="available",
                decision="create_story",
                receipt_key="x",
                occurred_at=1,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()
    async with SessionLocal() as db:
        clone = await db.get(Notification, first)
        db.add(
            Notification(
                user_id=owner,
                kind=clone.kind,
                media_type="plugin",
                media_id=uuid4(),
                title="dup",
                body="dup",
                event_at=1,
                dedupe_key="dup-revision",
                story_id=story.id,
                story_revision=1,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()


async def test_deleting_content_never_reopens_the_event_and_keeps_links(owner):
    [first] = await accept(owner, row("available", "plugin-update:example:2.0.0"))
    async with SessionLocal() as db:
        assert await delete_notice(db, owner, first)
        await db.commit()
    assert await accept(owner, row("available", "plugin-update:example:2.0.0")) == []
    [second] = await accept(owner, row("installed"))
    items, stories, receipts, _ = await snapshot(owner)
    by_id = {item.id: item for item in items}
    assert by_id[first].title == "" and by_id[second].previous_notification_id == first
    assert receipts == 2
    async with SessionLocal() as db:
        revisions = {
            r.revision: r
            for r in await db.scalars(
                select(NotificationStoryRevision).where(
                    NotificationStoryRevision.story_id == stories[0].id
                )
            )
        }
    assert revisions[1].title is None and revisions[1].body is None
    assert revisions[2].title == "Update installed"


async def test_legacy_available_notice_is_adopted_as_the_first_revision(owner):
    legacy = row("available", "plugin-update:example:2.0.0", story=None)
    del legacy["story"]
    [first] = await accept(owner, legacy)
    items, stories, _, _ = await snapshot(owner)
    assert items[0].story_id is None and not stories
    [second] = await accept(
        owner,
        row(
            "installed",
            story=reference(
                "installed",
                adopt_dedupe_key="plugin-update:example:2.0.0",
                adopt_phase="available",
            ),
        ),
    )
    items, stories, receipts, _ = await snapshot(owner)
    assert second == first and len(items) == 1 and receipts == 2
    assert (items[0].story_revision, stories[0].phase) == (2, "installed")


def plugin_lifecycle(event_type):
    return StoryLifecycleDeclaration(
        contract_version=NOTIFICATION_CONTRACT_VERSION,
        event_type=event_type,
        phases=(
            StoryPhase(id="open", label="Open"),
            StoryPhase(id="done", label="Done", terminal=True),
        ),
        initial_phases=("open",),
        transitions=(StoryTransition(from_phase="open", to_phase="done"),),
    )


async def test_plugin_uninstall_keeps_history_and_reinstall_starts_new_stories(owner):
    event_type = f"{PLUGIN}.job"
    lifecycle = plugin_lifecycle(event_type)

    async def emit(installation, phase, event_id):
        async with SessionLocal() as db:
            draft = NotificationDraft(
                kind="plugin",
                user_id=owner,
                entity_type="plugin",
                entity_id=uuid4(),
                occurred_at=int(time.time()),
                dedupe_key=event_id,
                title=f"Job {phase}",
                body="b",
                source=PLUGIN,
                story=StoryReference("job-1", phase, lifecycle),
            )
            created = await emit_interpreted(
                db,
                draft,
                NotificationInterpretation(
                    event_type, Trust.PRIVATE, "standard", "info", installation
                ),
            )
            await db.commit()
            return created

    first_installation, second_installation = uuid4(), uuid4()
    first = await emit(first_installation, "open", "e1")
    async with SessionLocal() as db:
        await retire_sources(db, PLUGIN)
        await db.commit()
    items, stories, receipts, _ = await snapshot(owner)
    assert [i.id for i in items] == [first] and len(stories) == 1 and receipts == 1
    assert stories[0].scope_key == str(first_installation)
    # A reinstall cannot revise the earlier installation's story: the same key is a new story.
    second = await emit(second_installation, "open", "e2")
    items, stories, _, _ = await snapshot(owner)
    assert second != first and len(stories) == 2 and len(items) == 2
    assert {s.scope_key for s in stories} == {str(first_installation), str(second_installation)}
    with pytest.raises(StoryTransitionError):
        await emit(second_installation, "open", "e3")


@pytest.fixture
async def admin(owner):
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.id == owner).values(is_admin=True))
        await db.commit()
    return owner


def update_info(**changes):
    return {
        "plugin_id": "example.updater",
        "current_version": "1.0.0",
        "available_version": "2.0.0",
        "update_available": True,
        **changes,
    }


async def notify(admin_id, **flags):
    async with SessionLocal() as db:
        await catalogues.notify_plugin_update(db, update_info(), **flags)
        await db.commit()
    items, stories, receipts, deliveries = await snapshot(admin_id)
    return items, stories, receipts


async def test_plugin_update_check_failure_and_install_advance_one_story(admin):
    items, stories, receipts = await notify(admin)
    assert [i.title for i in items] == ["Plugin update available: example.updater"]
    items, _, _ = await notify(admin)  # the next scheduled check is an exact replay
    assert len(items) == 1
    items, stories, receipts = await notify(admin, failed=True)
    assert (
        len(items) == 1
        and items[0].severity == "error"
        and "could not be activated" in items[0].body
    )
    await mark(items[0].id, read_at=7)
    items, stories, receipts = await notify(admin, installed=True)
    assert [i.story_revision for i in items] == [2, 3]
    assert items[1].previous_notification_id == items[0].id
    assert items[1].title == "Plugin update installed: example.updater"
    assert (stories[0].phase, receipts) == ("installed", 3)
    # A later failure report for a closed story creates nothing and does not raise.
    items, _, receipts = await notify(admin, failed=True)
    assert len(items) == 2 and receipts == 3


async def test_installing_a_version_that_was_never_announced_creates_nothing(admin):
    items, stories, receipts = await notify(admin, installed=True)
    assert (items, stories, receipts) == ([], [], 0)


async def test_install_result_reports_only_successful_updates(admin):
    await notify(admin)
    async with SessionLocal() as db:
        for result in (
            {"install_status": "installed", "status": "running", "version": "2.0.0"},
            {"install_status": "updated", "status": "rolled_back", "version": "1.0.0"},
            {"install_status": "updated", "status": "awaiting_permissions", "version": "2.0.0"},
        ):
            await catalogues.record_update_result(db, "example.updater", result)
    assert (await snapshot(admin))[1][0].phase == "available"
    async with SessionLocal() as db:
        await catalogues.record_update_result(
            db,
            "example.updater",
            {"install_status": "updated", "status": "running", "version": "2.0.0"},
        )
    assert (await snapshot(admin))[1][0].phase == "installed"


async def test_inbox_api_exposes_story_links():
    from src.api.routes.notifications import _read

    item = Notification(
        id=uuid4(),
        kind="plugin_update",
        media_type="plugin",
        media_id=uuid4(),
        title="t",
        body="b",
        event_at=1,
        created_at=1,
        severity="info",
        purpose="standard",
        required_trust=1,
        event_type="system.plugin.update",
        source="host",
        story_id=uuid4(),
        story_revision=2,
        previous_notification_id=uuid4(),
    )
    payload = _read(item)
    assert (payload["story_id"], payload["story_revision"]) == (item.story_id, 2)
    assert payload["previous_notification_id"] == item.previous_notification_id
