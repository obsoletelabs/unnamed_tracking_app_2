# Notification providers

The authenticated in-app inbox and external deliveries are separate destinations. Marking a notification read or dismissing it changes the inbox; it does not cancel external work. Deleting it cancels unsent work and removes its stored content. A delivery already in progress may finish.

External providers are optional and disabled by default. A provider preference also needs an active plugin installation and permission grant. Provider preferences are available through `/api/settings/notification-providers`; a new destination-management screen is not part of the core migration.

An existing Discord webhook is a PUBLIC destination. It can receive generic release facts, including the library's release title. It cannot receive personal selection/following details, account identifiers, watch history/status, ratings or security notices. The compatibility bridge does not treat a configured webhook as verified or as consent for richer announcements.

Save the webhook through the plugin's secret field, then explicitly enable the provider preference. Changing a stored plugin secret disables affected webhook destinations and suppresses their queued routing work; enable them again after configuration is complete. Uninstall preserves history and inactive preferences, but reinstall does not automatically reactivate old destinations.

Delivery is best-effort. The core persists attempts and retries transport failures up to three times. Removing or revoking a provider stops eligible queued work. A temporary runtime outage delays work until it is available or the notification's delivery validity expires.

The inbox defaults to 30 days of history. Retention preferences also support 180 and 365 days, or unlimited retention. Minimal deduplication receipts outlive notification content so old events do not reappear after deletion or retention cleanup.
