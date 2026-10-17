# Notification centre

Open **Notifications** to view your saved inbox. The bell and navigation badge show the full unread total, including notices outside the current page.

Use the category tabs, search field and source selector to narrow the inbox. Results load in pages; **Load more** keeps the current filters. Related notices from the same source, type and security context can be expanded together. **Read these updates** marks the displayed members read; members on other pages remain independent.

Expand a notice to see its message, source and exact event time. Read/unread changes stay with your account. **Dismiss** hides it from the inbox while external delivery continues. **Delete** removes its stored content and cancels unsent delivery work; a send already in progress may finish. Minimal deduplication records keep deleted events from returning.

Temporary plugin reminders are listed separately. They are plugin shortcuts, not saved notifications, and do not contribute to the durable unread total. Browser notifications are also separate from the inbox.

![Notification centre with grouped updates](../assets/notification-centre/desktop-light.png)

## Preferences and history

Open **Settings → Account → Notifications** or use the centre's **Notification settings** link. Configured providers come first, followed by notification types and inbox history. Calendar preferences have their own page. Followed-media filters live inside the relevant type's expansion, with one type switch and one routing configuration. Game release and unusual sign-in alerts have working host producers. Game sale/price-target controls appear only after a connected source emits live observations; a game's recorded purchase price is not a feed.

Each notification type has an on/off switch and a **Providers** expansion. Inside it, choose individual destinations and **Normal** or **Critical** delivery urgency. Turning off a type keeps these choices. A destination that cannot satisfy the type's trust or server policy is visibly unavailable. Sign-in alerts still require SECURE destinations when delivered as Normal.

### Unavailable choices and saved intent

A choice that currently has no effect is disabled, with the exact reason and the way to turn it back on shown beside it and read out by screen readers. Your saved choice is never changed: the switch keeps showing what you chose, and it applies again as soon as the dependency is restored. This covers:

- **Media filters.** A type is disabled while no media type it needs is followed (for example, the new season and movie/OVA/special types need **Anime** under *Followed media*), or when it needs a watch status and none is selected.
- **Routes.** A route is disabled while its notification type is off, its provider is disabled, the destination is turned off for your account, the destination is inactive, or it does not meet the type's trust or server policy.

Provider grants, destination proof, trust and privacy rules always stay authoritative; the explanation never grants anything.

Critical is a request for higher transport urgency, such as sound or Do Not Disturb override where supported. It does not change notification sensitivity, verification or permissions. The existing inbox and legacy webhook adapters support normal delivery only; a Critical choice is saved for a compatible provider, with that limitation shown beside the control.

**Your providers** controls external provider enrollment for your account and whether each existing destination is used. Inactive destinations and routing choices remain visible after removal; reinstall does not reclaim them automatically. The legacy webhook uses administrator configuration and generic public release facts. This section does not expose its credentials or pretend that it is a personal, verified endpoint. Personal endpoint enrollment and richer webhook consent follow in the provider work.

![Per-type provider routing and urgency](../assets/notification-centre/settings-routing-dark.png)

![Account provider controls](../assets/notification-centre/settings-providers-light.png)

Choose **Server default** or your own inbox duration, including **6 months**, **1 year** and unlimited history where allowed. The page shows the effective duration and any server maximum. A previous longer choice stays saved even when an administrator caps it; the cap is visible. New accounts inherit the server default. Existing saved choices remain overrides.

Administrators set `NOTIFICATION_RETENTION_DEFAULT_DAYS` (default 30) and `NOTIFICATION_RETENTION_MAXIMUM_DAYS` (default 0, no maximum) through deployment configuration. Zero for the default means unlimited history. A finite maximum also caps an unlimited choice. Inbox expiry does not cancel external work: content required by pending deliveries remains until those deliveries finish or expire, while it is hidden from the inbox. Explicit deletion has stronger cancellation behavior.

![Inbox retention and game preferences](../assets/notification-centre/settings-retention.png)

### Completed anime franchises

Two optional types follow anime you marked Watched or Favorite. Both start off, for new and existing accounts, and each is switched on separately:

- **Anime franchise seasons** — a new season was listed for that franchise. When it is on, these seasons are sent with this type instead of **Sequel announcements**, so each season arrives once.
- **Anime franchise movies, OVAs and specials** — a new movie, OVA or special was listed for that franchise.

Switching either on adds your account to the server's scheduled **Anime franchise check**, which looks at a few franchises at a time; your whole library may take several runs. Opening an anime's Related tab checks that franchise immediately. A franchise's first check only records what is already listed, and titles already in your library are not announced. A scheduled check never sends a Sequel announcement on your behalf: with Anime franchise seasons off, opening the title still announces a new season through Sequel announcements as before. Both types use the Anime media filter, routing, trust and inbox rules of the other release types, and appear under the **Seasons** and **Releases** inbox categories.

![Anime franchise notification types are off by default (Modern)](../assets/anime-franchise-checks/notification-types-modern.png)

![The same choices in Classic](../assets/anime-franchise-checks/notification-types-classic.png)

See [notification providers](notification-providers.md) for the existing external-delivery compatibility boundary.
