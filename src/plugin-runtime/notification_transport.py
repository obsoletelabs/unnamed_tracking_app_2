"""Trusted notification egress; never executed inside a third-party plugin worker."""

import json
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_discord(webhook: str, payload: dict) -> dict:
    """Only the private authenticated host broker can supply transport credentials."""
    parts = urlsplit(webhook)
    if (
        parts.scheme != "https"
        or parts.netloc not in {"discord.com", "discordapp.com"}
        or not re.fullmatch(r"/api/webhooks/[0-9]{5,25}/[A-Za-z0-9_-]{20,200}", parts.path)
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Protected Discord endpoint is invalid")
    if not isinstance(payload, dict) or set(payload) - {"allowed_mentions", "content", "embeds"}:
        raise ValueError("Protected Discord payload is invalid")
    if payload.get("allowed_mentions") != {"parse": []}:
        raise ValueError("Protected Discord mentions must be disabled")
    if "content" in payload:
        if (
            "embeds" in payload
            or not isinstance(payload["content"], str)
            or not 1 <= len(payload["content"]) <= 2000
        ):
            raise ValueError("Protected Discord content exceeds its bounds")
    else:
        embeds = payload.get("embeds")
        if not isinstance(embeds, list) or len(embeds) != 1 or not isinstance(embeds[0], dict):
            raise ValueError("Protected Discord embed is invalid")
        embed = embeds[0]
        bounds = {"title": 256, "description": 4096, "url": 2048, "timestamp": 64}
        if (
            not embed
            or set(embed) - bounds.keys()
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= bounds[key]
                for key, value in embed.items()
            )
        ):
            raise ValueError("Protected Discord embed exceeds its bounds")
    # Discord otherwise permits a 204 even when the message was not saved.
    # This is a fixed broker option; owners cannot supply arbitrary query actions.
    request = Request(
        webhook + "?wait=true",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                return {
                    "success": False,
                    "retryable": response.status >= 500,
                    "error": "discord_rejected",
                }
    except HTTPError as exc:
        return {
            "success": False,
            "retryable": exc.code >= 500 or exc.code in {408, 425, 429},
            "error": "discord_rejected",
        }
    except OSError:
        return {"success": False, "retryable": True, "error": "discord_unavailable"}
    return {"success": True, "retryable": False, "error": None}
