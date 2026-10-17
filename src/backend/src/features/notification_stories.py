"""Transaction-safe story identity, revisions and item links on the notification controller.

The controller records the dedupe receipt first (its unique constraint serializes replays) and then
calls `apply_revision` inside the same savepoint, so a rejected transition consumes no receipt.
Eligibility and transitions come from the published contract (`decide_revision`); this module only
persists the decision. Receipts are never touched here: deleting or retaining content cannot reopen
an event for replay.
"""

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.notification import Notification
from src.database.models.notification_story import (
    HOST_SCOPE,
    NotificationStory,
    NotificationStoryRevision,
)
from src.features.notification_contract import (
    STORY_PURPOSES,
    RevisionDecision,
    StoryHead,
    decide_revision,
    inbox_retention_cutoff,
)
from src.plugin_api.notification_story_contracts import (
    NOTIFICATION_CONTRACT_VERSION,
    StoryLifecycleDeclaration,
    StoryPhase,
    StoryTransition,
)

PLUGIN_UPDATE_EVENT_TYPE = "system.plugin.update"
PLUGIN_UPDATE_LIFECYCLE = StoryLifecycleDeclaration(
    contract_version=NOTIFICATION_CONTRACT_VERSION,
    event_type=PLUGIN_UPDATE_EVENT_TYPE,
    phases=(
        StoryPhase(id="available", label="Update available"),
        StoryPhase(id="installed", label="Update installed", terminal=True),
        StoryPhase(id="failed", label="Update failed", severity="error"),
        StoryPhase(id="resolved", label="Update resolved", terminal=True),
    ),
    initial_phases=("available",),
    transitions=(
        StoryTransition(from_phase="available", to_phase="available"),
        StoryTransition(from_phase="available", to_phase="installed"),
        StoryTransition(from_phase="available", to_phase="failed"),
        StoryTransition(from_phase="available", to_phase="resolved"),
        StoryTransition(from_phase="failed", to_phase="failed"),
        StoryTransition(from_phase="failed", to_phase="installed"),
        StoryTransition(from_phase="failed", to_phase="resolved"),
    ),
)


class StoryTransitionError(ValueError):
    """The requested phase is not a declared transition of the story's current state."""

    def __init__(self, decision: RevisionDecision) -> None:
        super().__init__(f"Story transition rejected: {decision.value}")
        self.decision = decision


@dataclass(frozen=True)
class StoryReference:
    """A host producer's story identity, phase and declared lifecycle for one event."""

    key: str
    phase: str
    lifecycle: StoryLifecycleDeclaration
    # Existing single-item notification that implicitly started this story before stories
    # existed (receipt key and the phase it represented). It is adopted as revision 1.
    adopt_dedupe_key: str | None = None
    adopt_phase: str | None = None


@dataclass(frozen=True)
class StoryOutcome:
    item: Notification
    decision: RevisionDecision
    story_id: UUID
    revision: int


async def _lock_story(
    db: AsyncSession,
    user_id: UUID,
    *,
    event_type: str,
    scope: str,
    key: str,
    source: str,
    phase: str,
) -> NotificationStory:
    """Row lock on the story identity; creation races resolve through the unique constraint."""
    identity = (
        NotificationStory.user_id == user_id,
        NotificationStory.event_type == event_type,
        NotificationStory.scope_key == scope,
        NotificationStory.story_key == key,
    )
    statement = select(NotificationStory).where(*identity).with_for_update()
    story = await db.scalar(statement.execution_options(populate_existing=True))
    if story is not None:
        return story
    now = int(time.time())
    await db.execute(
        pg_insert(NotificationStory)
        .values(
            user_id=user_id,
            event_type=event_type,
            scope_key=scope,
            story_key=key,
            source=source,
            phase=phase,
            revision=0,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_notification_story_identity")
    )
    story = await db.scalar(statement.execution_options(populate_existing=True))
    if story is None:  # pragma: no cover - the insert or a committed concurrent insert exists
        raise RuntimeError("Notification story could not be locked")
    return story


async def _adopt_legacy_head(
    db: AsyncSession, story: NotificationStory, reference: StoryReference, user_id: UUID
) -> None:
    """A pre-story notification with the story's first receipt key becomes revision 1."""
    if reference.adopt_dedupe_key is None or reference.adopt_phase is None:
        return
    legacy = await db.scalar(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.dedupe_key == reference.adopt_dedupe_key,
            Notification.story_id.is_(None),
            Notification.event_type == story.event_type,
        )
        .with_for_update()
    )
    if legacy is None or reference.lifecycle.phase(reference.adopt_phase) is None:
        return
    legacy.story_id = story.id
    legacy.story_revision = 1
    story.phase, story.revision = reference.adopt_phase, 1
    db.add(
        NotificationStoryRevision(
            story_id=story.id,
            revision=1,
            phase=reference.adopt_phase,
            notification_id=legacy.id,
            decision=RevisionDecision.CREATE_STORY.value,
            receipt_key=legacy.dedupe_key,
            occurred_at=legacy.event_at,
            accepted_at=legacy.created_at,
            title=legacy.title or None,
            body=legacy.body or None,
        )
    )
    await db.flush()


def _replace_content(head: Notification, candidate: Notification) -> None:
    """Revise the unread head: ID and unread state stay; the event key and time follow the event."""
    for field in (
        "dedupe_key",
        "title",
        "body",
        "poster_url",
        "event_at",
        "expires_at",
        "public_title",
        "public_body",
        "severity",
        "group_key",
    ):
        setattr(head, field, getattr(candidate, field))


async def apply_revision(
    db: AsyncSession,
    candidate: Notification,
    reference: StoryReference,
    *,
    preferences: dict[str, Any],
    now: int | None = None,
) -> StoryOutcome:
    """Persist one accepted event; raises `StoryTransitionError` (caller rolls the savepoint back).

    `candidate` is the not-yet-added item built from the event. It is added for a new story or a
    linked follow-up item and discarded when the unread head is updated in place.
    """
    now = int(time.time()) if now is None else now
    if candidate.user_id is None or candidate.purpose not in STORY_PURPOSES:
        raise ValueError("Only standard and security notifications can join stories")
    if reference.lifecycle.event_type != candidate.event_type:
        raise ValueError("Story lifecycle does not describe this event type")
    scope = str(candidate.source_installation_id or HOST_SCOPE)
    story = await _lock_story(
        db,
        candidate.user_id,
        event_type=candidate.event_type,
        scope=scope,
        key=reference.key,
        source=candidate.source,
        phase=reference.phase,
    )
    if story.revision == 0:
        await _adopt_legacy_head(db, story, reference, candidate.user_id)
    head_item = None
    if story.revision > 0:
        head_item = await db.scalar(
            select(Notification)
            .where(Notification.story_id == story.id, Notification.story_revision == story.revision)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    head = StoryHead(story.phase, story.revision, head_item) if head_item is not None else None
    decision = decide_revision(
        reference.lifecycle,
        head,
        reference.phase,
        now=now,
        retention_cutoff=inbox_retention_cutoff(preferences, now),
    )
    if decision in {RevisionDecision.REJECT_TRANSITION, RevisionDecision.REJECT_CLOSED}:
        raise StoryTransitionError(decision)
    phase = reference.lifecycle.phase(reference.phase)
    if phase is not None and phase.severity is not None:
        candidate.severity = phase.severity
    revision = story.revision + 1
    if decision != RevisionDecision.CREATE_STORY and head_item is None:
        raise RuntimeError("Story head item is missing")  # decide_revision requires a head
    if decision == RevisionDecision.UPDATE_IN_PLACE and head_item is not None:
        item = head_item
        _replace_content(item, candidate)
        item.story_revision = revision
    else:
        item = candidate
        item.story_id, item.story_revision = story.id, revision
        if decision == RevisionDecision.NEW_LINKED_ITEM and head_item is not None:
            item.previous_notification_id = head_item.id
        db.add(item)
    story.phase, story.revision, story.updated_at = reference.phase, revision, now
    await db.flush()
    db.add(
        NotificationStoryRevision(
            story_id=story.id,
            revision=revision,
            phase=reference.phase,
            notification_id=item.id,
            decision=decision.value,
            receipt_key=candidate.dedupe_key,
            occurred_at=candidate.event_at,
            accepted_at=now,
            title=candidate.title,
            body=candidate.body,
        )
    )
    await db.flush()
    return StoryOutcome(item, decision, story.id, revision)
