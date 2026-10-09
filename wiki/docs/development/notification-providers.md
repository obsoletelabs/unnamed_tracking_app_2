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

Personal destination enrollment and registered event sources use separate host boundaries. The legacy bridge does not expose verification or sensitive delivery.


## Protected Discord destinations (contract 1.1.2)

Registration additionally accepts `transport`, defaulting to `legacy`. `discord_webhook` selects host-protected transport. It does not create a destination or grant trust. Changing an active registration's transport requires unregistering it first. Namespaced registration is serialized and remains installation-bound.

Owner-only `POST /api/settings/notification-providers/webhook-destinations` accepts provider ID, Discord HTTPS webhook URL and label. There are at most 20 active endpoints per owner/provider. The two supported Discord hosts require an exact webhook path, without authority credentials, custom ports, query actions or fragments. Configuration uses existing host encryption; routing metadata exposes only labels, IDs, revisions, consent and availability. Enrollment/routing changes require the current provider delivery grant and live installation. URLs are never returned to plugins or UI.

PATCH supports label, enabled state and `share_followed_media`. Rich-media confirmation belongs to the exact endpoint revision and never changes PUBLIC trust. Initial layouts support approved title/body/release facts, occurrence time and application link, with the followed/selected fact only after confirmation. Artwork stays excluded until an independent public image projection is reviewed. Account identifiers, ratings, watch state, private price thresholds and security/recovery content are not part of the public projection. Replacing a webhook requires fresh enrollment. DELETE erases its encrypted configuration and retires pending work while retaining history/preferences.

The action returns `NotificationFieldLayout`: `style` is `plain` or `embed`, and `fields` contains unique references to `title`, `body`, `event_at` and `link`. Extra text, arbitrary URLs, embeds, mentions and raw payloads are rejected. The host builds and bounds the payload from the approved projection, disables Discord mentions and uses existing notification URL precedence. This transport delivers Normal urgency; it does not claim to bypass notification suppression.

Private runtime `notification-layouts` executes the renderer without legacy transport authority. After rendering, the coordinator rechecks the current leased attempt, exact endpoint revision, deletion/expiry, source lifecycle, preferences, grant and installation. Only then does the authenticated host submit `notification-transports/discord` to the trusted runtime broker. Its credential-bearing envelope never reaches `supervisor.execute`, plugin storage or the public gateway. Existing `PLUGIN_RUNTIME_DISCORD_EGRESS` policy remains authoritative. The broker checks current installation/grant, refuses redirects, bounds socket time and returns sanitized retry outcomes. Global attempts/retries/state remain core-owned.

Disable, uninstall, registration withdrawal and provider-grant revocation deactivate affected destinations, clear richer consent and suppress unsent work. Same-installation destinations can be explicitly activated again without replaying that work; consent needs fresh confirmation. Reinstallation requires new enrollment with credentials and cannot automatically reclaim old configuration. Retained labels/preferences/history stay visible.

SECURE destinations and raw output require further protected proof, purpose and explicit high-risk permission support; this initial public webhook contract does not accept them. Browser push remains separate work. Migration `a1a6b04b3606` follows `c4771032f3c4`, defaults existing registrations to legacy and preserves unrelated tables. Downgrade refuses protected registrations instead of converting them to legacy senders.
