"""Canonical content for host-produced plugin update and game price notifications.

The host producers keep their identities, receipts and routing; this module only describes what a
recipient sees (title, summary, typed facts and host-route actions) in the shared content
contract so every surface, the live story page and digests render the same record.
"""

from decimal import Decimal
from urllib.parse import quote, urlencode
from uuid import UUID

from src.plugin_api.notification_story_contracts import (
    ContentAction,
    ContentFact,
    HostRouteTarget,
    MoneyFactValue,
    NotificationContent,
    TextFactValue,
)

PLUGINS_SETTINGS_ROUTE = "/settings?area=administration&section=plugins"

_UPDATE_TITLES = {
    "available": "Plugin update available",
    "failed": "Plugin update failed",
    "installed": "Plugin update installed",
    "resolved": "Plugin update resolved",
}


def plugin_update_route(plugin_id: str) -> str:
    """The Plugin Manager page, narrowed to one plugin; never a hard-coded host."""
    return f"{PLUGINS_SETTINGS_ROUTE}&{urlencode({'plugin': plugin_id}, quote_via=quote)}"


def plugin_update_title(phase: str, plugin_id: str) -> str:
    return f"{_UPDATE_TITLES[phase]}: {plugin_id}"


def plugin_update_summary(phase: str, version: str, current_version: str | None) -> str:
    if phase == "available":
        return f"Version {version} is available (installed: {current_version})."
    if phase == "failed":
        return (
            f"Version {version} could not be activated. The previous package is retained; "
            "inspect Plugin Manager diagnostics."
        )
    if phase == "installed":
        return f"Version {version} is installed."
    return f"Version {version} is no longer pending for this plugin."


def plugin_update_content(
    phase: str, plugin_id: str, version: str, current_version: str | None
) -> NotificationContent:
    """One plugin update revision with a direct link to that plugin in the Plugin Manager."""
    facts = [
        ContentFact(
            key="plugin", label="Plugin", value=TextFactValue(kind="text", value=plugin_id)
        ),
        ContentFact(
            key="target_version",
            label="Version",
            value=TextFactValue(kind="text", value=version),
        ),
    ]
    if current_version and phase in {"available", "failed"}:
        facts.insert(
            1,
            ContentFact(
                key="installed_version",
                label="Installed",
                value=TextFactValue(kind="text", value=current_version),
            ),
        )
    return NotificationContent(
        title=plugin_update_title(phase, plugin_id),
        summary=plugin_update_summary(phase, version, current_version),
        facts=tuple(facts),
        actions=(
            ContentAction(
                id="open_plugin",
                label="Open in Plugin Manager",
                style="primary",
                target=HostRouteTarget(kind="host_route", path=plugin_update_route(plugin_id)),
            ),
        ),
    )


def open_game_action(game_id: UUID) -> ContentAction:
    return ContentAction(
        id="open_game",
        label="Open game",
        style="primary",
        target=HostRouteTarget(kind="host_route", path=f"/games/{game_id}"),
    )


def game_price_content(
    *,
    title: str,
    summary: str,
    game_id: UUID,
    currency: str,
    amount: Decimal,
    regular_amount: Decimal | None,
    market: str,
    platform: str,
) -> NotificationContent:
    """Facts come only from the validated quote; nothing here derives or infers a price."""
    facts = [
        ContentFact(
            key="price",
            label="Price",
            value=MoneyFactValue(kind="money", amount=format(amount, "f"), currency=currency),
        )
    ]
    if regular_amount is not None:
        facts.append(
            ContentFact(
                key="regular_price",
                label="Regular price",
                value=MoneyFactValue(
                    kind="money", amount=format(regular_amount, "f"), currency=currency
                ),
            )
        )
    facts.append(
        ContentFact(key="market", label="Market", value=TextFactValue(kind="text", value=market))
    )
    facts.append(
        ContentFact(
            key="platform", label="Platform", value=TextFactValue(kind="text", value=platform)
        )
    )
    return NotificationContent(
        title=title,
        summary=summary,
        facts=tuple(facts),
        actions=(open_game_action(game_id),),
    )
