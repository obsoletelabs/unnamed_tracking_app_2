"""Host producers (plugin updates, game releases and prices) on the shared story/content contract."""

import time
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from src.api.routes.plugin_manager import catalogues
from src.database.models.game import Game, GameStatus
from src.database.models.notification import Notification
from src.database.models.notification_story import NotificationStory, NotificationStoryRevision
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notification_controller import NotificationEvent, emit
from src.features.notification_producer_content import (
    PLUGINS_SETTINGS_ROUTE,
    plugin_update_content,
)
from src.plugin_api.notification_story_contracts import NotificationContent


@pytest.fixture
async def admin():
    user_id = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=user_id,
                username=user_id.hex,
                email=f"{user_id}@example.test",
                password_hash="unused",
                is_admin=True,
            )
        )
        await db.commit()
    try:
        yield user_id
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


def info(plugin_id="example.one", version="2.0.0", **changes):
    return {
        "plugin_id": plugin_id,
        "current_version": "1.0.0",
        "available_version": version,
        "update_available": True,
        **changes,
    }


async def notify(update_info, **flags):
    async with SessionLocal() as db:
        await catalogues.notify_plugin_update(db, update_info, **flags)
        await db.commit()


async def resolve(plugin_id):
    async with SessionLocal() as db:
        count = await catalogues.resolve_plugin_update_stories(db, plugin_id)
        await db.commit()
        return count


async def items_of(user_id):
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.event_at, Notification.story_revision, Notification.title)
            )
        )


async def revisions_of(user_id):
    async with SessionLocal() as db:
        return list(
            await db.scalars(
                select(NotificationStoryRevision)
                .join(NotificationStory, NotificationStory.id == NotificationStoryRevision.story_id)
                .where(NotificationStory.user_id == user_id)
                .order_by(NotificationStoryRevision.accepted_at, NotificationStoryRevision.revision)
            )
        )


def stored_content(revision) -> NotificationContent:
    return NotificationContent.model_validate(revision.content)


async def test_update_revisions_carry_typed_facts_and_a_plugin_manager_action(admin):
    await notify(info())
    await notify(info(), failed=True)
    revisions = await revisions_of(admin)
    assert [r.phase for r in revisions] == ["available", "failed"]
    content = stored_content(revisions[0])
    assert {f.key for f in content.facts} == {"plugin", "installed_version", "target_version"}
    (action,) = content.actions
    assert action.target.kind == "host_route"
    assert action.target.path == f"{PLUGINS_SETTINGS_ROUTE}&plugin=example.one"
    # The app root is never the destination and no host name is embedded.
    assert not action.target.path.startswith("//") and "://" not in action.target.path
    assert "could not be activated" in stored_content(revisions[1]).summary


async def test_replayed_availability_keeps_one_stable_event_identity(admin):
    await notify(info())
    # The lifecycle can observe the same target release again with refreshed installed-version
    # metadata while an automatic update is starting. It is still the same availability event.
    await notify(info(current_version="1.0.1"))
    revisions = await revisions_of(admin)
    assert [revision.phase for revision in revisions] == ["available"]
    assert len(await items_of(admin)) == 1


async def test_unread_update_evolves_in_place_and_read_update_keeps_history(admin):
    await notify(info())
    await notify(info(), failed=True)
    assert [(i.story_revision, i.severity) for i in await items_of(admin)] == [(2, "error")]
    async with SessionLocal() as db:
        await db.execute(
            update(Notification).where(Notification.user_id == admin).values(read_at=5)
        )
        await db.commit()
    await notify(info(), installed=True)
    first, second = await items_of(admin)
    assert first.read_at == 5 and second.previous_notification_id == first.id
    assert second.title == "Plugin update installed: example.one"
    assert second.read_at is None


async def test_several_plugins_have_separate_items_with_their_own_links(admin):
    for plugin in ("example.one", "example.two", "example.three"):
        await notify(info(plugin))
    paths = {stored_content(r).actions[0].target.path for r in await revisions_of(admin)}
    assert paths == {
        f"{PLUGINS_SETTINGS_ROUTE}&plugin={p}"
        for p in ("example.one", "example.two", "example.three")
    }
    assert len(await items_of(admin)) == 3


async def test_resolved_closes_open_stories_for_one_plugin_and_keeps_history(admin):
    await notify(info("example.one"))
    await notify(info("example.two"))
    await notify(info("example.two", "3.0.0"))
    await notify(info("example.two", "3.0.0"), failed=True)
    assert await resolve("example.two") == 2
    async with SessionLocal() as db:
        phases = {
            (s.story_key, s.phase)
            for s in await db.scalars(
                select(NotificationStory).where(NotificationStory.user_id == admin)
            )
        }
    assert phases == {
        ("update:example.one:2.0.0", "available"),
        ("update:example.two:2.0.0", "resolved"),
        ("update:example.two:3.0.0", "resolved"),
    }
    assert len(await items_of(admin)) == 3  # nothing was deleted
    # Nothing is left to resolve and a replay is a no-op.
    assert await resolve("example.two") == 0


async def test_installed_stories_are_terminal_and_not_resolved(admin):
    await notify(info())
    await notify(info(), installed=True)
    await resolve("example.one")
    assert [r.phase for r in await revisions_of(admin)] == ["available", "installed"]


async def test_resolving_a_plugin_prefix_does_not_touch_similarly_named_plugins(admin):
    await notify(info("example.one"))
    await notify(info("example.one_extra"))
    assert await resolve("example.one") == 1
    async with SessionLocal() as db:
        phases = {
            s.story_key: s.phase
            for s in await db.scalars(
                select(NotificationStory).where(NotificationStory.user_id == admin)
            )
        }
    assert phases["update:example.one_extra:2.0.0"] == "available"


def test_update_content_is_valid_for_every_phase():
    for phase in ("available", "failed", "installed", "resolved"):
        content = plugin_update_content(phase, "example.one", "2.0.0", "1.0.0")
        assert content.title.endswith("example.one") and len(content.actions) == 1


async def owned_game(db, user_id):
    game = Game(
        user_id=user_id,
        title="Example Game",
        sort_title="example",
        folder_location=uuid4().hex,
        status=GameStatus.WISHLIST,
        release_date=datetime.now(timezone.utc).date(),
    )
    db.add(game)
    await db.flush()
    return game


QUOTE = dict(
    source="integration.store",
    observation_id="sale-1",
    currency="USD",
    amount="19.99",
    regular_amount="39.99",
    market="US",
    platform="PC",
)


async def test_sale_and_price_hit_content_uses_only_validated_quote_facts(admin):
    async with SessionLocal() as db:
        game = await owned_game(db, admin)
        event = NotificationEvent(
            "game.sale.started", admin, "game", game.id, int(time.time()), "sale-key", QUOTE
        )
        sale = await emit(db, event)
        hit = await emit(
            db,
            replace(
                event,
                type="game.price.threshold_hit",
                dedupe_key="hit-key",
                data={
                    **QUOTE,
                    "observation_id": "hit-1",
                    "threshold": "20.00",
                    "previous_amount": "25.00",
                    "threshold_id": "wish-1",
                },
            ),
        )
        await db.commit()
        for identity in (sale, hit):
            notification = await db.get(Notification, identity)
            revision = await db.scalar(
                select(NotificationStoryRevision).where(
                    NotificationStoryRevision.notification_id == identity
                )
            )
            content = stored_content(revision)
            assert notification.title == game.title and notification.story_id is not None
            assert content.actions[0].target.path == f"/games/{game.id}"
            price = next(f for f in content.facts if f.key == "price").value
            assert (price.amount, price.currency) == ("19.99", "USD")
            # The private target is only in the owner's text, never a fact or the public copy.
            assert "20.00" not in "".join(f.model_dump_json() for f in content.facts)
            assert "20.00" not in (notification.public_body or "")
            assert "target" not in (notification.public_body or "")
        assert any(
            f.key == "regular_price"
            for f in stored_content(
                await db.scalar(
                    select(NotificationStoryRevision).where(
                        NotificationStoryRevision.notification_id == sale
                    )
                )
            ).facts
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"regular_amount": None}, "regular price"),
        ({"regular_amount": "10.00"}, "below its regular price"),
        ({"amount": 19.99}, "decimal strings"),
        ({"currency": "usd"}, "currency"),
    ],
)
async def test_invalid_sales_are_still_rejected_without_a_story(admin, change, message):
    async with SessionLocal() as db:
        game = await owned_game(db, admin)
        quote = {k: v for k, v in {**QUOTE, **change}.items() if v is not None}
        event = NotificationEvent(
            "game.sale.started", admin, "game", game.id, int(time.time()), "bad", quote
        )
        with pytest.raises(ValueError, match=message):
            await emit(db, event)
        await db.rollback()
    assert await items_of(admin) == []
