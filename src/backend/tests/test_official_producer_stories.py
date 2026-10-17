"""Host conformance for the official Jellyfin sync-attention story (companion producer).

The documents mirror `official/jellyfin-media-sync/attention.py` in the plugins repository, which
is built, signed with a throwaway key and run in that repository's tests.
"""

import time
from hashlib import sha256

import pytest
from sqlalchemy import select, update

from src.database.models.notification import Notification
from src.database.models.notification_story import NotificationStory
from src.database.session import SessionLocal
from src.features.notification_source_policy import retire_sources
from src.plugin_api.notification_story_contracts import NOTIFICATION_CONTRACT_VERSION
from tests.test_plugin_notification_contract import Source, source_fixture  # noqa: F401

V = NOTIFICATION_CONTRACT_VERSION


def sync_registration(plugin):
    event_type = f"{plugin}.sync-attention"
    return {
        "contract_version": V,
        "event_type": event_type,
        "label": "Jellyfin sync needs attention",
        "description": "A Jellyfin account cannot sync until its problem is fixed.",
        "story_key_prefix": "sync:",
        "grouping": {"strategy": "key", "merge": "digest"},
        "lifecycle": {
            "contract_version": V,
            "event_type": event_type,
            "phases": [
                {"id": "attention", "label": "Needs attention", "severity": "warning"},
                {"id": "resolved", "label": "Syncing again", "terminal": True},
            ],
            "initial_phases": ["attention"],
            "transitions": [
                {"from_phase": "attention", "to_phase": "attention"},
                {"from_phase": "attention", "to_phase": "resolved"},
            ],
        },
        "facts": [
            {"key": "account", "label": "Account", "kind": "text"},
            {"key": "server", "label": "Server", "kind": "text", "required": False},
        ],
    }


def sync_event(plugin, phase, summary, account="acc1"):
    return {
        "contract_version": V,
        "event_type": f"{plugin}.sync-attention",
        "event_id": sha256(f"{phase}:{account}:{summary}".encode()).hexdigest()[:32],
        "occurred_at": int(time.time()),
        "story": {"key": f"sync:{account}", "phase": phase},
        "grouping_key": sha256(f"sync\0{account}".encode()).hexdigest()[:32],
        "content": {
            "title": "Jellyfin sync needs attention"
            if phase == "attention"
            else "Jellyfin sync resumed",
            "summary": summary,
            "facts": [
                {"key": "account", "label": "Account", "value": {"kind": "text", "value": "Home"}},
                {"key": "server", "label": "Server", "value": {"kind": "text", "value": "Den"}},
            ],
            "actions": [
                {
                    "id": "open_accounts",
                    "label": "Open Jellyfin accounts",
                    "target": {
                        "kind": "host_route",
                        "path": "/plugins/official.jellyfin-media-sync/accounts",
                    },
                }
            ],
        },
    }


async def stories_of(source):
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(NotificationStory).where(NotificationStory.user_id == source.owner)
            )
        )


async def register(source):
    return await source.call("notification_sources.register", sync_registration(source.plugin))


async def test_the_producer_documents_are_accepted_and_an_unread_problem_is_revised(source):
    assert (await register(source))["registered"] is True
    first = await source.publish(sync_event(source.plugin, "attention", "Check the token."))
    assert first["created"] is True
    replay = await source.publish(sync_event(source.plugin, "attention", "Check the token."))
    assert replay["created"] is False
    await source.publish(sync_event(source.plugin, "attention", "The certificate changed."))
    (item,) = await source.notifications()
    assert item.story_revision == 2 and "certificate" in item.body
    (story,) = await stories_of(source)
    assert story.phase == "attention" and story.story_key == "sync:acc1"


async def test_read_problem_keeps_history_and_recovery_links_a_new_item(source):
    await register(source)
    await source.publish(sync_event(source.plugin, "attention", "Check the token."))
    async with SessionLocal() as db:
        await db.execute(
            update(Notification).where(Notification.user_id == source.owner).values(read_at=3)
        )
        await db.commit()
    await source.publish(sync_event(source.plugin, "resolved", "Synchronisation resumed."))
    older, newer = await source.notifications()
    assert older.read_at == 3 and "Check the token" in older.body
    assert newer.previous_notification_id == older.id and newer.read_at is None
    (story,) = await stories_of(source)
    assert story.phase == "resolved"
    with pytest.raises(ValueError):
        await source.publish(sync_event(source.plugin, "attention", "Again.", "acc1"))


async def test_accounts_never_share_a_story_and_resolving_without_a_problem_is_rejected(source):
    await register(source)
    await source.publish(sync_event(source.plugin, "attention", "One.", "acc1"))
    await source.publish(sync_event(source.plugin, "attention", "Two.", "acc2"))
    assert len(await source.notifications()) == 2 and len(await stories_of(source)) == 2
    with pytest.raises(ValueError):
        await source.publish(sync_event(source.plugin, "resolved", "Never failed.", "acc3"))
    assert len(await source.notifications()) == 2


async def test_disabling_the_plugin_keeps_the_history_and_stops_new_events(source):
    await register(source)
    await source.publish(sync_event(source.plugin, "attention", "Check the token."))
    async with SessionLocal() as db:
        await retire_sources(db, source.plugin)
        await db.commit()
    assert len(await source.notifications()) == 1 and len(await stories_of(source)) == 1
    with pytest.raises(PermissionError):
        await source.publish(sync_event(source.plugin, "attention", "Another problem."))
    assert len(await source.notifications()) == 1
