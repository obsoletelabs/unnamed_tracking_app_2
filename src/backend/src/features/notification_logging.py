"""Keep bearer proof/opt-out tokens out of Uvicorn's request access log."""

import logging


class NotificationLinkFilter(logging.Filter):  # pylint: disable=too-few-public-methods
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            path = record.args[2]
            if isinstance(path, str) and path.partition("?")[0] in {
                "/api/notifications/email-verify",
                "/api/notifications/email-unsubscribe",
            }:
                record.args = (*record.args[:2], path.partition("?")[0], *record.args[3:])
        return True


def protect_notification_link_logs() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(entry, NotificationLinkFilter) for entry in logger.filters):
        logger.addFilter(NotificationLinkFilter())
