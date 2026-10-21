"""Redact bearer values and suppress routine successful plugin gateway access logs."""

import logging
import re

_CALENDAR_FEED_PATH = re.compile(r"^(/api/calendar/feed/)[^/?]+(\.ics)$")
_LIVE_LINK_PREFIXES = ("/api/notifications/live/", "/api/notifications/stories/by-link/")
_REDACTED_LIVE_LINK = "/api/notifications/live/<redacted>"
_QUERY_SECRET_PATHS = (
    "/api/notifications/email-verify",
    "/api/notifications/email-unsubscribe",
    "/api/auth/oidc/callback",
)


def redact_request_target(target: str) -> str:
    """Drop live-link tokens, credential-bearing query strings and the calendar feed token from a logged path."""
    if target.startswith(_LIVE_LINK_PREFIXES):
        return _REDACTED_LIVE_LINK
    path, _, _query = target.partition("?")
    if path in _QUERY_SECRET_PATHS or path.startswith("/api/auth/oidc/callback/"):
        return path
    feed = _CALENDAR_FEED_PATH.match(path)
    if feed:
        return f"{feed.group(1)}[redacted]{feed.group(2)}"
    return target


class NotificationLinkFilter(logging.Filter):  # pylint: disable=too-few-public-methods
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            path = record.args[2]
            if isinstance(path, str):
                if (
                    record.levelno == logging.INFO
                    and record.args[1] == "POST"
                    and path.partition("?")[0] == "/api/plugins/runtime/gateway"
                    and record.args[4] == 200
                ):
                    return False
                record.args = (*record.args[:2], redact_request_target(path), *record.args[3:])
        return True


def protect_notification_link_logs() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(entry, NotificationLinkFilter) for entry in logger.filters):
        logger.addFilter(NotificationLinkFilter())
