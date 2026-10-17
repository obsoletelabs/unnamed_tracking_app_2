"""Plugin update inbox items expose a safe in-app link that survives every lifecycle phase."""

from urllib.parse import quote

import pytest
from sqlalchemy import update
from test_notification_producer_stories import admin, info, items_of, notify, resolve  # noqa: F401

from src.api.routes.notifications import _read
from src.database.models.notification import Notification
from src.database.session import SessionLocal
from src.features.notification_producer_content import (
    plugin_update_route,
    plugin_update_route_from_dedupe,
)


@pytest.mark.parametrize("suffix", ["", ":failed", ":installed", ":resolved"])
def test_route_is_recovered_from_every_phase_key(suffix):
    key = f"plugin-update:example.one:2.0.0{suffix}"
    assert plugin_update_route_from_dedupe(key) == plugin_update_route("example.one")


def test_route_rejects_unrelated_or_malformed_keys_and_escapes_ids():
    assert plugin_update_route_from_dedupe("game-price:1") is None
    assert plugin_update_route_from_dedupe("plugin-update:2.0.0") is None
    assert plugin_update_route_from_dedupe("plugin-update:") is None
    route = plugin_update_route_from_dedupe("plugin-update:a b&x=1:1.0.0")
    assert route is not None and f"plugin={quote('a b&x=1', safe='')}" in route
    assert "&x=1" not in route.split("plugin=")[1]


async def test_every_phase_item_links_to_the_plugin_and_read_dismiss_keep_it(admin):  # noqa: F811
    await notify(info())
    assert [_read(i)["link"] for i in await items_of(admin)] == [plugin_update_route("example.one")]
    async with SessionLocal() as db:
        await db.execute(
            update(Notification)
            .where(Notification.user_id == admin)
            .values(read_at=5, dismissed_at=6)
        )
        await db.commit()
    await notify(info(), failed=True)
    await notify(info(), installed=True)
    items = await items_of(admin)
    assert len(items) >= 2
    assert {_read(i)["link"] for i in items} == {plugin_update_route("example.one")}
    assert items[0].read_at == 5 and items[0].dismissed_at == 6


async def test_uninstall_resolution_keeps_history_and_link(admin):  # noqa: F811
    await notify(info())
    assert await resolve("example.one") == 1
    items = await items_of(admin)
    assert items and all(_read(i)["link"] == plugin_update_route("example.one") for i in items)


async def test_non_plugin_notifications_have_no_link(admin):  # noqa: F811
    await notify(info())
    item = (await items_of(admin))[0]
    item.kind = "game_release"
    assert _read(item)["link"] is None
