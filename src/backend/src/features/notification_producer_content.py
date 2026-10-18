"""Canonical content for host-produced plugin update and game price notifications.

The host producers keep their identities, receipts and routing; this module only describes what a
recipient sees (title, summary, typed facts and host-route actions) in the shared content
contract so every surface, the live story page and digests render the same record.
"""

import re
from decimal import Decimal
from urllib.parse import quote, urlencode
from uuid import UUID

from src.plugin_api.notification_story_contracts import (
    ContentAction,
    ContentFact,
    HostRouteTarget,
    MarkdownBlock,
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


_UPDATE_DEDUPE_PREFIX = "plugin-update:"
_UPDATE_PHASE_SUFFIXES = tuple(f":{phase}" for phase in ("installed", "failed", "resolved"))


def plugin_update_route_from_dedupe(dedupe_key: str) -> str | None:
    """The Plugin Manager route for a plugin update inbox row, from its dedupe key.

    Keys are ``plugin-update:<plugin id>:<version>`` with an optional phase suffix.
    """
    if not dedupe_key.startswith(_UPDATE_DEDUPE_PREFIX):
        return None
    rest = dedupe_key[len(_UPDATE_DEDUPE_PREFIX) :]
    for suffix in _UPDATE_PHASE_SUFFIXES:
        if rest.endswith(suffix):
            rest = rest[: -len(suffix)]
            break
    plugin_id, _, version = rest.rpartition(":")
    return plugin_update_route(plugin_id) if plugin_id and version else None


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


_BATCH_ACTION_LIMIT = 5
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>()])")


def _escape(value: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", value)


def _public_link(public_url: str, path: str) -> str | None:
    """An absolute link only when the deployment's public URL is known; never a dead path."""
    return f"{public_url.rstrip('/')}{path}" if public_url else None


def _batch_line(item: dict[str, str], status_label: str, public_url: str) -> str:
    plugin_id = item["plugin_id"]
    link = _public_link(public_url, plugin_update_route(plugin_id))
    name = f"[{_escape(plugin_id)}]({link})" if link else _escape(plugin_id)
    line = f"- {status_label}: {name} ({item['old_version']} → {item['new_version']})"
    if item.get("reason"):
        line += f" — {_escape(item['reason'])}"
    changelog = item.get("changelog_url", "")
    if changelog.startswith("https://") and not set(changelog) & set(" ()<>"):
        line += f" ([release notes]({changelog}))"
    return line


def plugin_update_batch_content(
    items: list[dict[str, str]], status_labels: dict[str, str], public_url: str
) -> NotificationContent:
    """One combined update notice: per-plugin lines that link to the plugin and its changelog.

    Without a configured public URL the text says where to find the Plugin Manager instead of
    showing a relative path that nothing can open. Host-route actions become buttons or links on
    surfaces that know the public URL.
    """
    lines = [_batch_line(item, status_labels[item["status"]], public_url) for item in items]
    manage = _public_link(public_url, PLUGINS_SETTINGS_ROUTE)
    footer = (
        f"Manage plugins [here]({manage})."
        if manage
        else "Manage plugins in Settings, Administration, Plugins. Set the public app URL "
        "to get direct links in external notifications."
    )
    actions = [
        ContentAction(
            id="manage_plugins",
            label="Manage plugins",
            style="primary",
            target=HostRouteTarget(kind="host_route", path=PLUGINS_SETTINGS_ROUTE),
        )
    ]
    for index, item in enumerate(items[: _BATCH_ACTION_LIMIT - 1], start=1):
        actions.append(
            ContentAction(
                id=f"plugin_{index}",
                label=f"Open {item['plugin_id']}"[:40],
                target=HostRouteTarget(
                    kind="host_route", path=plugin_update_route(item["plugin_id"])
                ),
            )
        )
    return NotificationContent(
        title=f"Plugin updates: {len(items)} plugin(s) updated or need attention",
        summary="Plugin update batch results:",
        blocks=(MarkdownBlock(type="markdown", markdown="\n".join(lines) + "\n\n" + footer),),
        actions=tuple(actions),
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
