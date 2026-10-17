"""Pure, side-effect-free building blocks of the notification diagnostics interface.

Everything here is data or a pure function: scenario fixtures, representative canonical content,
the catalogue of safe reason codes with their next actions, and the redaction applied to every
string the diagnostics interface returns. Nothing imports a session, a provider or a clock.
"""

from __future__ import annotations

import re
from typing import Any, Final

from src.plugin_api.notification_story_contracts import NotificationContent

VARIANTS: Final = ("basic", "rich", "image", "actions", "long")
SENSITIVE_PURPOSES: Final = frozenset({"recovery", "verification", "security"})
FAILURES: Final = (
    "none",
    "transient",
    "rate_limited",
    "unavailable",
    "invalid_config",
    "permanent",
)
PLACEHOLDER_IMAGE: Final = "https://example.com/diagnostics/poster.png"
REDACTED_SENSITIVE: Final = "[redacted: sensitive content is never rendered by diagnostics]"

# code -> (label, next action). Unknown codes never echo free text.
REASONS: Final[dict[str, tuple[str, str]]] = {
    "accepted": ("The event was accepted as new.", "No action needed."),
    "deduplicated": (
        "An identical event was already received, so it was ignored.",
        "No action needed; duplicates are expected after producer retries.",
    ),
    "create_story": ("A new story was started.", "No action needed."),
    "update_in_place": ("The unread story item was updated in place.", "No action needed."),
    "new_linked_item": (
        "A new linked item was created because the earlier item was read or expired.",
        "No action needed.",
    ),
    "reject_transition": (
        "The story phase change is not allowed by the lifecycle.",
        "Fix the producer so it only sends declared transitions.",
    ),
    "reject_closed": (
        "The story is in a terminal phase and accepts no more changes.",
        "Start a new story if the situation has recurred.",
    ),
    "immediate": ("No grouping applies; the event is delivered as is.", "No action needed."),
    "coalesce": ("Related events are combined into one message.", "No action needed."),
    "digest": (
        "The event joins the next scheduled digest.",
        "Change timing settings to receive it sooner.",
    ),
    "urgent_bypass": (
        "Urgency skipped the grouping window.",
        "Lower the urgency if early delivery is not wanted.",
    ),
    "quiet_hours": (
        "Quiet hours hold external delivery until they end.",
        "Adjust quiet hours or mark the route urgent.",
    ),
    "muted": ("This route is muted, so nothing is queued.", "Re-enable the route to receive it."),
    "route_disabled": (
        "The type is disabled for this destination.",
        "Enable the route in notification routing.",
    ),
    "destination_inactive": (
        "The destination is inactive or disabled.",
        "Re-enable the destination.",
    ),
    "provider_disabled": (
        "The provider is not enabled for this account.",
        "Enable the provider in notification settings.",
    ),
    "preference_disabled": (
        "The notification type is turned off in your preferences.",
        "Turn the type on in notification settings.",
    ),
    "blocked_by_admin": (
        "An administrator blocked this provider or event type.",
        "Ask an administrator to lift the restriction.",
    ),
    "trust_too_low": (
        "The destination is not verified enough for this content.",
        "Verify the destination to raise its trust level.",
    ),
    "sensitive_not_permitted": (
        "Sensitive content needs a verified, explicitly permitted external destination.",
        "Verify the destination and allow sensitive messages for it.",
    ),
    "test_bound_to_destination": (
        "Test messages only go to the destination they were created for.",
        "Send the test from that destination's own control.",
    ),
    "eligible": ("The destination is eligible.", "No action needed."),
    "no_destination": (
        "No destination of yours could be evaluated.",
        "Add or enable a destination first.",
    ),
    "sent": ("Delivered.", "No action needed."),
    "retry_wait": ("A temporary failure; the dispatcher will retry.", "No action needed."),
    "provider_error": (
        "The provider reported a failure.",
        "Check the provider's configuration and availability, then retry.",
    ),
    "rate_limited": (
        "The provider asked the dispatcher to slow down.",
        "Wait for the automatic retry or reduce how many messages are sent at once.",
    ),
    "provider_unavailable": (
        "The provider was not reachable or not installed.",
        "Check that the provider or plugin is installed, authorized and running.",
    ),
    "invalid_config": (
        "The provider is not configured correctly.",
        "Review and save the provider's configuration, then retry.",
    ),
    "attempts_exhausted": (
        "All delivery attempts were used.",
        "Fix the underlying problem, then retry the delivery.",
    ),
    "failed_permanent": (
        "The provider refused the message permanently.",
        "Check the destination and provider configuration, then send a new test.",
    ),
    "dispatch_error": (
        "An internal error interrupted delivery.",
        "Retry; report it to an administrator if it repeats.",
    ),
    "edit_failed": (
        "The earlier remote message could not be edited; a follow-up was used.",
        "No action needed.",
    ),
    "source_revoked": ("The producer's permission was withdrawn.", "Re-authorize the plugin."),
    "routing_changed": (
        "Routing changed after the work was queued.",
        "No action needed unless the change was unintended.",
    ),
    "provider_revoked": ("The provider was removed.", "Reinstall or re-register the provider."),
    "installation_changed": (
        "The plugin installation changed.",
        "Re-enable the destination for the new installation.",
    ),
    "user_inactive": ("The account is inactive.", "Reactivate the account."),
    "authorization_unavailable": (
        "The provider's authorization could not be confirmed.",
        "Check the plugin's grants and runtime.",
    ),
    "transport_security_required": (
        "The transport does not meet the required security level.",
        "Use a verified, secure destination.",
    ),
    "recovery_expired": ("The recovery message expired before delivery.", "Request a new one."),
    "superseded": ("A newer revision replaced this delivery.", "No action needed."),
    "revision_in_flight": ("An earlier revision is still sending.", "No action needed."),
    "revision_policy": ("Revisions are not sent by server policy.", "No action needed."),
    "follow_up_policy": (
        "Follow-ups are limited to important updates by server policy.",
        "No action needed.",
    ),
    "actions_via_story_link": (
        "Some actions do not fit this provider, so they are offered on the story page.",
        "Open the story link to use every action.",
    ),
    "actions_dropped": (
        "This provider cannot show the actions and no story link is available.",
        "Use a provider that supports links or action buttons.",
    ),
    "images_dropped": (
        "This provider cannot show the images (or external images are disabled).",
        "No action needed; images appear on the story page.",
    ),
    "truncated": (
        "The text was shortened to fit the provider's limits.",
        "Open the story link for the full text.",
    ),
    "rich_text_plain": (
        "Formatted blocks and facts were converted to plain text.",
        "No action needed.",
    ),
    "rich_content_plain_text": (
        "This provider receives a plain-text version of rich content.",
        "No action needed.",
    ),
    "edit_unsupported": (
        "The provider cannot edit sent messages, so updates are sent as follow-ups.",
        "No action needed.",
    ),
    "reference_unavailable": (
        "The earlier remote message cannot be located, so a follow-up was used.",
        "No action needed.",
    ),
    "shared_message": (
        "The earlier message was shared by several items and cannot be edited.",
        "No action needed.",
    ),
    "edit_failed_earlier": (
        "An earlier edit failed, so follow-ups are used for this message.",
        "No action needed.",
    ),
    "declaration_ignored": (
        "The provider's capability declaration is not used for this built-in provider.",
        "No action needed.",
    ),
    "edit_not_implemented": (
        "The provider declared message edits but does not implement them.",
        "Update the plugin so its declaration matches what it implements.",
    ),
    "contribution_markdown_to_plain": (
        "A plugin-supplied template was converted to plain text.",
        "No action needed.",
    ),
    "contribution_unsupported": (
        "A plugin-supplied template does not suit this provider and was ignored.",
        "No action needed.",
    ),
    "already_reconciled": ("This revision was already presented.", "No action needed."),
}
UNRECOGNIZED: Final = (
    "The delivery ended with an unrecognized reason.",
    "Contact an administrator.",
)
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_SECRETS: Final = (
    (re.compile(r"https?://[^\s<>\"']*", re.I), "url"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (
        re.compile(r"(?i)\b(?:authorization\s*[:=]\s*)?(?:bearer|basic)\s+\S+"),
        "[redacted]",
    ),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|otp|code|pin)\b"
            r"\s*(?:is|[:=])?\s*\S+"
        ),
        "[redacted]",
    ),
    (re.compile(r"[A-Za-z0-9_\-]{24,}"), "[redacted]"),
    (re.compile(r"(?<![\w.])\d{6,8}(?![\w.])"), "[code]"),
)


def _redact_url(match: re.Match[str]) -> str:
    """Keep only the origin; paths and queries routinely carry bearer tokens."""
    parts = match.group(0).split("/", 3)
    origin = "/".join(parts[:3]).split("?", 1)[0].split("#", 1)[0]
    return f"{origin}/…" if len(parts) > 3 or "?" in match.group(0) else origin


def redact(value: str, limit: int = 400) -> str:
    """Remove credentials, one-time codes, recipient addresses and link tokens from free text."""
    text = value
    for pattern, replacement in _SECRETS:
        text = pattern.sub(_redact_url if replacement == "url" else replacement, text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def redact_value(value: Any) -> Any:
    """Recursively redact every string of a JSON-like structure."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


def reason(code: str | None) -> dict[str, str]:
    """A safe, actionable description of a stored reason; unknown text is never echoed."""
    clean = code if code and _CODE.fullmatch(code) else None
    label, action = REASONS.get(clean or "", UNRECOGNIZED)
    return {"code": clean if clean in REASONS else "unrecognized", "label": label, "action": action}


def test_content(variant: str) -> NotificationContent:
    """Representative canonical content for a variant; the same document test sends render."""
    body: dict[str, Any] = {
        "title": "Test: example release available",
        "summary": "Example release: A new episode is available. This is a delivery test.",
    }
    if variant in {"rich", "image", "actions"}:
        body["blocks"] = [{"type": "markdown", "markdown": "**Example** season finale is out."}]
        body["facts"] = [
            {"key": "episode", "label": "Episode", "value": {"kind": "text", "value": "S01E08"}},
            {"key": "score", "label": "Score", "value": {"kind": "integer", "value": 91}},
        ]
    if variant == "image":
        body["images"] = [
            {
                "role": "artwork",
                "source": {"kind": "external", "url": PLACEHOLDER_IMAGE},
                "alt": "Example artwork",
            }
        ]
    if variant == "actions":
        body["actions"] = [
            {
                "id": "open",
                "label": "Open notifications",
                "style": "primary",
                "target": {"kind": "host_route", "path": "/notifications"},
            },
            {
                "id": "docs",
                "label": "Documentation",
                "target": {"kind": "link", "url": "https://example.com/docs"},
            },
        ]
    if variant == "long":
        body["summary"] = " ".join(["Long diagnostic paragraph used to exercise truncation."] * 60)
    return NotificationContent.model_validate(body)


def variant_of(group_key: str | None) -> str:
    """The content variant a test notification carries (stored in its group key)."""
    name = (group_key or "").removeprefix("variant:")
    return name if name in VARIANTS else "basic"


# Scenario fixtures. They are data only: the simulator maps them to a synthetic, unsaved event.
FIXTURES: Final[dict[str, dict[str, Any]]] = {
    "standard": {
        "label": "Ordinary release",
        "description": "A normal media release at the current time.",
        "event_type": "media.episode.aired",
    },
    "urgent": {
        "label": "Urgent (critical severity)",
        "description": "Critical severity asks to skip quiet hours and grouping.",
        "event_type": "media.episode.aired",
        "severity": "critical",
        "urgency": "critical",
    },
    "quiet_hours": {
        "label": "During quiet hours",
        "description": "Quiet hours covering the evaluation time hold external delivery.",
        "event_type": "media.episode.aired",
        "quiet_hours": True,
    },
    "duplicate_ingestion": {
        "label": "Duplicate ingestion",
        "description": "The producer sends the same event several times.",
        "event_type": "media.episode.aired",
        "duplicates": 3,
    },
    "story_transitions": {
        "label": "Story transitions",
        "description": "A plugin update moves through its lifecycle, including a rejected change after it closed.",
        "event_type": "system.plugin.update",
        "phases": ["available", "available", "failed", "installed", "available"],
    },
    "digest": {
        "label": "Digest window",
        "description": "A sale feed that is grouped into a periodic digest.",
        "event_type": "game.sale.started",
    },
    "provider_transient": {
        "label": "Provider temporary failure",
        "description": "The provider fails twice, then succeeds.",
        "event_type": "media.episode.aired",
        "failure": "transient",
    },
    "provider_rate_limited": {
        "label": "Provider rate limit",
        "description": "The provider throttles every attempt.",
        "event_type": "media.episode.aired",
        "failure": "rate_limited",
    },
    "provider_unavailable": {
        "label": "Provider unavailable",
        "description": "The provider or plugin runtime cannot be reached.",
        "event_type": "media.episode.aired",
        "failure": "unavailable",
    },
    "provider_invalid_config": {
        "label": "Invalid provider configuration",
        "description": "The provider rejects its saved configuration.",
        "event_type": "media.episode.aired",
        "failure": "invalid_config",
    },
    "provider_permanent": {
        "label": "Permanent provider refusal",
        "description": "The provider refuses the message for good.",
        "event_type": "media.episode.aired",
        "failure": "permanent",
    },
    "recovery": {
        "label": "Account recovery (simulation only)",
        "description": "Shows why recovery messages need a verified external destination. "
        "Never sent and never rendered.",
        "event_type": "auth.password_reset",
        "purpose": "recovery",
        "sendable": False,
    },
    "verification": {
        "label": "Verification code (simulation only)",
        "description": "Shows eligibility for possession proof. Never sent and never rendered.",
        "event_type": "auth.verification",
        "purpose": "verification",
        "sendable": False,
    },
}
