# Notification centre

Open **Notifications** to view your saved inbox. The bell and navigation badge show the full unread total, including notices outside the current page.

Use the category tabs, search field and source selector to narrow the inbox. Results load in pages; **Load more** keeps the current filters. Related notices from the same source, type and security context can be expanded together. **Read these updates** marks the displayed members read; members on other pages remain independent.

Expand a notice to see its message, source and exact event time. Read/unread changes stay with your account. **Dismiss** hides it from the inbox while external delivery continues. **Delete** removes its stored content and cancels unsent delivery work; a send already in progress may finish. Minimal deduplication records keep deleted events from returning.

Temporary plugin reminders are listed separately. They are plugin shortcuts, not saved notifications, and do not contribute to the durable unread total. Browser notifications are also separate from the inbox.

![Notification centre with grouped updates](../assets/notification-centre/desktop-light.png)

## Preferences and history

Open **Settings → Notifications** or use the centre's **Notification settings** link. Existing media controls remain available, alongside switches for game releases, game sales, price targets and unusual sign-in alerts. Game sales and targets require a connected producer that supplies live price observations; a game's recorded purchase price is not a feed.

Choose **Server default** or your own inbox duration, including **6 months**, **1 year** and unlimited history where allowed. The page shows the effective duration and any server maximum. A previous longer choice stays saved even when an administrator caps it; the cap is visible. New accounts inherit the server default. Existing saved choices remain overrides.

Administrators set `NOTIFICATION_RETENTION_DEFAULT_DAYS` (default 30) and `NOTIFICATION_RETENTION_MAXIMUM_DAYS` (default 0, no maximum) through deployment configuration. Zero for the default means unlimited history. A finite maximum also caps an unlimited choice. Inbox expiry does not cancel external work: content required by pending deliveries remains until those deliveries finish or expire, while it is hidden from the inbox. Explicit deletion has stronger cancellation behavior.

![Inbox retention and game preferences](../assets/notification-centre/settings-retention.png)

See [notification providers](notification-providers.md) for the existing external-delivery compatibility boundary.
