"""Keep bearer proof, opt-out, calendar-feed and OIDC exchange values out of the access log."""

import logging
import re

_CALENDAR_FEED_PATH = re.compile(r"^(/api/calendar/feed/)[^/?]+(\.ics)$")
_QUERY_SECRET_PATHS = (
    "/api/notifications/email-verify",
    "/api/notifications/email-unsubscribe",
    "/api/auth/oidc/callback",
)


def redact_request_target(target: str) -> str:
    """Drop credential-bearing query strings and the calendar feed token from a logged path."""
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
                record.args = (*record.args[:2], redact_request_target(path), *record.args[3:])
        return True


def protect_notification_link_logs() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(entry, NotificationLinkFilter) for entry in logger.filters):
        logger.addFilter(NotificationLinkFilter())
