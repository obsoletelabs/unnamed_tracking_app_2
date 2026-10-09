"""Core construction of protected Discord payloads from approved field references."""

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from src.plugin_api.notification_contracts import NotificationFieldLayout

from .base import NotificationMessage


def normalize_discord_webhook(value: str) -> str:
    """Do not accept credentials in authority, redirects, arbitrary hosts or query actions."""
    parts = urlsplit(value.strip())
    if (
        parts.scheme != "https"
        or parts.netloc not in {"discord.com", "discordapp.com"}
        or not re.fullmatch(r"/api/webhooks/[0-9]{5,25}/[A-Za-z0-9_-]{20,200}", parts.path)
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Enter an HTTPS Discord webhook URL without query parameters")
    return f"https://{parts.netloc}{parts.path}"


def discord_payload(
    layout: NotificationFieldLayout, message: NotificationMessage, link: str
) -> dict:
    """Provider output cannot supply text, URLs, mentions, embeds or credential values."""
    fields = set(layout.fields)
    values = {
        "title": message.title,
        "body": message.body,
        "link": f"{link.rstrip('/')}/notifications" if link else "",
    }
    try:
        stamp = datetime.fromtimestamp(message.event_at, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        stamp = ""
    values["event_at"] = stamp
    payload: dict = {"allowed_mentions": {"parse": []}}
    if layout.style == "plain":
        text = "\n".join(values[field] for field in layout.fields if values[field])
        payload["content"] = text[:2000] or "Notification"
        return payload
    embed = {}
    if "title" in fields:
        embed["title"] = values["title"][:256]
    if "body" in fields:
        embed["description"] = values["body"][:4096]
    if "link" in fields and values["link"]:
        embed["url"] = values["link"]
    if "event_at" in fields and stamp:
        embed["timestamp"] = stamp
    # Discord rejects empty embeds; the fallback is a fixed host-owned label.
    payload["embeds"] = [embed or {"description": "Notification"}]
    return payload
