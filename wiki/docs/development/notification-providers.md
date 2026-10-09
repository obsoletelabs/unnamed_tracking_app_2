# Plugin notification providers

The [notification core](notification-core.md) owns routing, persistence and delivery lifecycle. Plugins cannot create notification database rows, override preferences or control retries. Plugin implementations live in `obsoletelabs/unnamed_tracking_app_plugins`.

## Registration and compatibility

An enabled plugin calls `notification_providers.register` with a namespaced `provider_id`, display `name` and `action_id`. Registration is bound to the current installation and requires the existing provider capability. Dispatch checks the live `notification_providers.deliver` grant and installation state. Permission revocation and uninstall invalidate registration; uninstall also deactivates destinations. History and preferences remain. Reinstallation does not reclaim an old destination or automatically replay notifications.

Existing provider preferences default to disabled. The compatibility bridge represents an opted-in shared webhook as a PUBLIC external destination. Only a host-produced public release projection can reach it. Arbitrary plugin text, session warnings and other private/security content are suppressed. Legacy endpoints cannot assert verification or richer per-webhook media consent.

## Delivery boundary

The core creates work for a concrete destination revision. The existing job loop claims that work, then rechecks trust, preferences, deployment policy, installation identity, grants and expiry. Runtime outages defer work without consuming a physical delivery attempt. A transport failure uses exponential backoff, with at most three attempts; each attempt is persisted separately.

API contract **1.1.2** adds a private host-to-runtime notification-delivery operation. Attempt, installation and user authorization context are outside plugin action values. The plugin receives a minimized rendering DTO: notification ID, kind, approved title/body, media reference and event time. Public release references do not disclose the owner's library row ID. Credentials never appear in this DTO, ordinary settings, diagnostics or delivery errors.

The reference Discord transport accepts only supported Discord webhook URLs, caps content at 2,000 characters and requires `PLUGIN_RUNTIME_DISCORD_EGRESS=true`. Generic action calls cannot enable Discord transport by submitting a delivery payload. Arbitrary plugin network access remains disabled.

## Legacy secrets

The secret field is write-only **to the frontend**. Existing `plugin.storage` authorization still permits a plugin to read its own stored secrets; this is a compatibility mechanism, not a host credential vault. Changing a legacy plugin secret disables its installation's webhook destinations, increments their revisions, clears proof/consent and suppresses queued work before the runtime write. A failed write leaves them disabled. The user must explicitly enable the destination again. This prevents old authorization from moving silently to a replacement webhook.

Generic plugin destination configuration, verified external destinations, high-risk sensitive delivery grants and custom event-source registration are not exposed by this compatibility bridge.
