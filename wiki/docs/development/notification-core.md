# Notification core

Notification orchestration lives in `features/notification_controller.py`, with adjacent destination, policy, lifecycle and provider modules. Existing media, season, session, plugin-update and plugin-send producers pass through this boundary. It flushes within the caller's transaction and never performs network delivery. UI polling and bounded offline discovery share deduplication; the existing job loop scans up to four active users per tick with a per-user time limit and a rotating cursor.

## Objects and ownership

| Object | Responsibility |
| --- | --- |
| Event | Minimal producer facts identifying what happened and its entity. |
| Notification | A host-enriched interpretation for one user, with sensitivity, purpose, severity, source and grouping key. |
| Receipt | Unique user/event identity and optional fact fingerprint, without private content. |
| Destination | Concrete user-owned endpoint, revision, privacy, proof, context, activation and consent. |
| Provider | Available mechanism that delivers an approved representation. |
| Delivery | Work for one notification and destination revision. |
| Attempt | Durable record of one physical send, independent of the provider. |

Existing media handlers retain their enrichment through a compatibility draft. Typed game events resolve the recipient's owned game. `game.released` uses its known release date; `game.sale.started` requires an identified live quote and regular price; `game.price.threshold_hit` requires an identified downward threshold crossing. Decimal strings, currency, market, platform, source and observation identity are validated. A conflicting replay is rejected. Historical purchase prices are not market quotes, and the core does not invent a price feed. User-protected titles remain valid public release labels.

## Routing security

Trust belongs to the destination: PUBLIC, PRIVATE, or SECURE. PRIVATE becomes SECURE only with current host verification evidence matching the endpoint revision. The authenticated internal inbox is SECURE. Changing an endpoint must invalidate old proof and consent. Plugins cannot assert verification.

Notification sensitivity and purpose are separate. Session anomaly notices require SECURE destinations. Recovery requires a SECURE **external** destination with explicit recovery support; an internal inbox cannot receive recovery secrets. Built-in email verifies external possession through host-owned codes. Production reset and invite features remain plugins and are not introduced by SMTP enrollment.

Routing applies type and destination preferences, user ownership, activation, provider availability and administrative restrictions. Users may disable security notices. `NOTIFICATION_BLOCKED_PROVIDERS` and `NOTIFICATION_BLOCKED_TYPES` block routes; `NOTIFICATION_MINIMUM_TRUST` raises the destination floor (0/1/2). Administrative policy never lowers a notification requirement.

Host media handlers store a separate public release projection. It omits selection/following, account and viewing information and personal price targets. Richer media disclosure needs consent for that exact concrete endpoint revision and still does not promote its trust. The legacy webhook bridge supports only the generic projection. Current plugin providers cannot receive SECURE content.

## Delivery and inbox lifecycle

Delivery states are `pending`, `processing`, `sent`, `retry_wait`, `failed_permanent`, `suppressed` and `cancelled`. Claims use row locks and a 90-second lease; expired attempts are recorded as unknown. A send has a 35-second timeout and up to three attempts. Authorization is rechecked before transport. One failing provider does not prevent other eligible destinations from receiving their work. Errors contain safe reason codes, not credentials.

External delivery is at least once: a crash after a remote send and before local acknowledgement can produce a duplicate. Delete cancels unclaimed work and invalidates claims; cancellation after transport starts is best-effort. Dismiss only hides the inbox entry. Group keys are persisted, with individual notifications and deliveries; external digest/coalescing is not implemented.

Content retention is independent of transport validity and deduplication. Security work expires after one day and ordinary work after 30 days. Inbox retention remains user-configurable. Minimal receipts currently have no expiry.

## Migration and validation

Alembic revision `22ff87d4d01c` follows `d8338e79fbbd` in the single migration history. It preserves notification IDs and read state, creates inbox destinations and dedupe receipts, and records the migrated inbox deliveries. Unbound historical external work is suppressed instead of replayed. Back up before upgrading. Downgrade refuses data with multiple destinations per provider because the old schema cannot represent it safely. Complete pre-existing schema adoption is idempotent; partial schema adoption requires an explicit repair.

`tests/test_notification_core.py` covers routing, projections, verification evidence, preferences, deduplication, deletion, retries, provider isolation and game events. `tests/test_notification_migration.py` exercises populated upgrade and downgrade against PostgreSQL. Runtime tests cover the protected transport and API compatibility. Companion conformance checks build and install real `.utp` packages through the public versioned boundary.

## Inbox query and retention contract

`GET /api/notifications` retains `limit`, `offset` and `unread_only`, and adds bounded `category`, `search`, `source` and `severity` filters. Response metadata includes the full matching `total`, category `counts`, full inbox `unread`, `next_offset` and owner-scoped sources. Ordering uses event time plus ID. Search escapes SQL wildcard characters. Category counts reflect search/source/severity before category selection; the unread badge remains global to the owner's visible inbox.

Notification representations additionally carry event type, source, severity, purpose, trust requirement, group key, creation time and read time. Grouping is presentation only and keeps every durable notification and delivery intact. The frontend groups within a local calendar day and matching source/type/purpose/trust/severity. Pages can split groups; group operations apply only to displayed members.

`GET /api/notifications/policy` returns server default/maximum, requested/effective days, inheritance and whether a maximum is applied. The existing preference service stores `notification_retention_inherit`; older stored durations retain override semantics. Server configuration uses the central registry with bounded 0–3650-day values. Expired queued notices are hidden from the inbox while their transport content is retained; terminal expired rows can be removed while receipts persist. Explicit deletion still cancels work. Browser push state is independent.

## Personal routing controls

`GET /api/settings/notification-providers/destinations` returns owner-scoped destination identifiers, effective trust, internal/external context, eligibility, registered provider state and type descriptions. Inactive owner records remain visible. It never returns endpoint keys, addresses, configuration references, verification methods or credentials. Registration availability is not a live transport health probe; delivery still checks grants, installation and provider availability. Eligibility is computed with the same disclosure policy as dispatch, independently of personal on/off choices.

The existing preference service accepts `notification_routes`: a map of event type to destination UUID to `{enabled, urgency}`. It is bounded to 128 types and 512 choices; urgency is `normal` or `critical`, default Normal. Extra security/verification/capability assertions are rejected. Type and destination global opt-outs still apply. Every attempt rechecks the route, so disabling it suppresses queued work independently of inbox history. A foreign user's UUID in an owner's preferences grants no authority over that destination.

The core records requested and effective delivery urgency. Built-in SMTP supports Critical through high-priority email headers, subject to mail-client behavior. The inbox and legacy plugin adapters deliver Normal while retaining the requested choice. Urgency cannot weaken sensitivity, destination trust, administrative policy or plugin permissions. The UI distinguishes concrete personal email destinations from shared legacy webhooks.

## Built-in SMTP enrollment and proof

`features/smtp_configuration.py` resolves the central registry's six `SMTP_*` fields with ENV precedence over `AppIntegrationSettings`. Persisted authentication passwords and endpoint addresses use existing Fernet encryption. The host-only adapter selects the concrete destination revision; it never falls back to a user's account email or arbitrary first address. Owner routing metadata exposes a label and masked email, never endpoint keys, full addresses or credentials.

The email enrollment routes under `/api/settings/notification-providers/email-destinations` create, patch and remove owner endpoints; verification request, confirmation and revocation are separate operations. Client-supplied trust/proof fields are rejected. A user row lock serializes enrollment and issuance limits across workers; proof records use revision-bound keyed digests, encrypted transient codes, expiry, single-use state and a five-attempt limit. Changing an address clears proof, recovery consent, codes and unsent delivery claims. Removing it additionally erases endpoint configuration and retires its identity.

Verification is a reserved host interpretation, targeted to exactly one external endpoint. Generic notice content contains no code and is not inbox-visible. Only the built-in email adapter resolves the transient encrypted code for that bound notification and revision. The existing scheduler erases expired code material; proof remains valid until address change or revocation.

Verification email also carries a possession link when the user's notification URL resolves. Its encrypted, purpose-bound token identifies the same owner, destination revision and ten-minute challenge as the code. `GET /api/notifications/email-verify` validates without consuming proof; explicit form POST uses the existing locked verifier. It neither signs into an account nor opts a destination into recovery. Replacement, revocation, expiry and prior consumption invalidate both methods. The public confirmation page uses no-store/no-referrer headers, a restrictive CSP and no external resources. Access-log filtering removes bearer queries from verification and unsubscribe requests.

The dispatcher independently checks transport eligibility and resolves current built-in configuration again before I/O. Plaintext can carry ordinary notifications, with a warning. Security, recovery and verification require TLS except for development mode or a literal local SMTP server IP (loopback/RFC1918/ULA). `localhost` or DNS resolution alone is not proof of the local exception. STARTTLS and implicit TLS use verified SSL contexts without plaintext fallback.

Revision `efc50aae7b57` adds email configuration, encrypted endpoints, challenge records and normal-default urgency fields after `22ff87d4d01c`. It does not alter unrelated game-note tables. Downgrade refuses active email destinations or unsent SMTP work. `tests/test_notification_email.py` exercises real isolated loopback SMTP delivery plus ownership, routing, transport exceptions, proof expiry/replay/revision/revocation/limits, safe DTOs and transient versus permanent failures.

## SMTP default ports and scoped opt-out

An unset `smtp_port` selects the mode default (implicit TLS 465, STARTTLS 587, plaintext 25). The UI preserves saved/manual custom values and ENV locks; Use default port clears the override rather than persisting a suggested number.

`PUBLIC_APP_URL` is an optional HTTPS public FQDN resolved through the existing ENV handler, setup and Application settings. Notification URL precedence is concrete destination override, personal `notification_url` preference, configured public FQDN, then the owner's `last_app_url`. The authenticated `/api/auth/me` remembers the request origin only for browser-session actors and excludes cross-site requests; API keys, plugins and anonymous traffic cannot update it. This never modifies OIDC configuration or redirects. HTTP origins are accepted for personal/detected URLs so LAN-IP access remains possible; shared defaults require HTTPS and a public-style FQDN, excluding IPs/local hostnames. DNS is not resolved by the link renderer. URLs reject credentials, paths, queries, fragments and header-unsafe characters. `PATCH /api/settings/notification-providers/destinations/{id}/url` updates only an owned active external destination and cannot change proof/trust. Plugins receive no destination credential access through it. The SMTP opt-out footer and protected plugin field layouts use this resolver.

Revisions `ef1fa87c9382` and `a4456747e462` add the shared URL and per-user/per-destination origins in a linear history after `efc50aae7b57`. Adoption preserves existing URL values. These changes do not alter unrelated game-note tables. Destination retirement is shared lifecycle logic used by address changes and opt-out, preserving one cancellation boundary.

For ordinary-purpose email, the SMTP adapter creates a 90-day encrypted purpose-bound token containing owner, concrete endpoint, address identity and revision. It adds an intact `List-Unsubscribe` URL plus a footer, with no personal information in the URL/page. The public host route `/api/notifications/email-unsubscribe` has no account authentication dependency: GET reads only, while explicit form POST exercises the narrow opt-out authority. Responses are no-store, no-referrer and restricted by CSP, with no external resources. Verification/recovery/security messages omit subscription headers; no RFC 8058 one-click header is advertised.

Opt-out disables the endpoint, suppresses pending/processing/retry work and advances its revision. Current same-address verification evidence is carried to that new revision; no new proof or trust is granted. Pending challenges are retired. Repeated POST is harmless while disabled. After authenticated reactivation, old tokens fail revision checks. Removed/replaced endpoints and forged/expired/foreign-purpose tokens cannot change state. Inbox, other endpoints and history remain independent. Plugins cannot create or consume this host-only authority.

## Delivery diagnostics and settings

`POST /api/settings/notification-providers/smtp/test` is administrator-only and accepts one normalized recipient address. It uses the saved/ENV SMTP configuration and shared SMTP transport with a fixed, generic example. It does not enroll the recipient, grant verification or create an inbox notice. Failures return sanitized transport reason codes. `POST /api/settings/notification-providers/destinations/{id}/test` is owner-only and creates a short-lived delivery targeted to that exact active endpoint through the normal preference, trust, availability and dispatcher checks. A provider failure cannot change its verification state or send the example to other destinations.

Both controls share a persisted limit of ten tests per account per hour, serialized on the existing user row. Payload-free receipts count failed SMTP attempts too. Test emails omit subscription headers. The administrative diagnostic confirms transport acceptance, not final mailbox receipt; personal external tests expose the normal queued delivery state.

Settings separates Account → Notifications from Administration → Notifications and from Calendar. The account page orders configured providers first, then type routing and inbox history; the administrator page owns server SMTP. Both email entry forms initially use the signed-in account's configured email without automatically enrolling or verifying it. Source filters remain compatible with existing preferences inside media-type expansions. Sale/price-target hooks have no invented feed and appear in the type catalogue only after actual account observations.

## Registered plugin notification sources (contract 1.1.2)

`notification_sources.register` requires the matching capability and registers a retained type in the caller's `<plugin_id>.…` namespace. `notification_sources.unregister` uses the same capability. The host reserves auth/security/destination/system/media/game/core namespaces. A declaration supplies a label, description, PRIVATE or SECURE required trust, standard/security/recovery purpose, severity, bounded plain-text title/body templates, and at most 32 named scalar parameters. Security/recovery require SECURE. Generic plugin text has no PUBLIC projection; the host's reviewed public media projection remains separate.

`notifications.emit` requires `notifications.emit` and accepts only event type, bounded producer dedupe key, Unix occurrence time, flat typed facts and optional grouping key. The gateway supplies the authenticated recipient and current installation. Caller-selected users, destinations, credentials, trust, HTML and delivery state are rejected. Templates accept exact declared names, without attribute/index access, conversions or format specifications. Facts must exactly match the declared scalar types; booleans do not satisfy integer parameters. There are at most 32 retained types per plugin and 100 newly accepted events per plugin/account/hour, serialized through the existing account row lock. Quotas use server acceptance time, not caller occurrence time.

SECURE source registration and emission additionally require an explicit `notifications.sensitive` grant. Neither `notifications` nor `api.full` implies it. Existing permission review classifies it as critical/highly privileged. Real auth types remain host-reserved; demonstrations use namespaced synthetic events. Recovery-purpose notifications require a current SECURE external destination with explicit recovery opt-in and suitable transport. They never enter the authenticated inbox. Source code receives no address or SMTP credentials.

The controller persists interpretation, source installation, payload-free dedupe identity, grouping key and normal delivery rows. Replay with changed facts fails; accepted identities survive inbox deletion and reinstall. Namespaced types have independent type/route preferences rather than the legacy `plugin.notice` switch. Owner settings expose actual permitted registered types; inactive types remain visible for recipients with history. Existing senders and legacy `notifications.send` remain compatible.

Disable, unregister, uninstall and administrative grant revocation retire source records and suppress pending/processing/retry deliveries. History/preferences remain. Re-registration can accept new events but does not replay old deliveries or reclaim destination credentials/consent. Dispatch rechecks source installation and grants before transport lookup and immediately before sending. A source runtime outage alone does not invalidate an already accepted fact; explicit lifecycle retirement does. Transport already started when revocation commits is best effort, like destination deletion.

Migration `c4771032f3c4` follows `24bc18feb852`, adds retained type records, nullable source-installation identity and receipt acceptance time, and preserves existing notification and unrelated game-note tables. Old receipts receive acceptance time zero; all new host receipts use server time. Downgrade refuses retained source identities instead of silently losing authorization/history.


Notification lifecycle replay is separately granted and contains only scoped metadata. See [Plugin API lifecycle replay](plugin-api-v1.md#notification-lifecycle-replay-114) for ordering, scope and retention. Inbox expiry does not erase its independently retained audit history, and no delivery credentials or content enter the feed.

## Operating delivery and replay

The existing jobs loop performs discovery, expiring verification/session cleanup,
delivery and lifecycle publication. Keep it running after deployment; opening the
inbox is not required for external delivery. Each stage is bounded, so backlogs
can take several ticks to drain. Restarting a worker preserves queued work and
the lifecycle outbox.

`retry_wait` means the core will retry when its recorded next-attempt time is
due. `failed_permanent` is terminal; changing configuration does not replay
old terminal work. A provider outage can delay its work while other eligible
destinations continue. Revocation, endpoint revision changes and deletion
invalidate queued claims; already-started external I/O remains best effort.
Never interpret a successful renderer action as a successful delivery.

The three retention boundaries are independent: personal inbox visibility,
transport validity, and lifecycle audit history. Minimal dedupe receipts outlive
all three and currently have no expiry. Deleting an inbox item does not erase its
dedupe receipt or independently retained plugin lifecycle metadata. Disabling or
uninstalling a plugin retains history and inactive preferences; reinstallation
requires renewed destination activation and consent, without automatic replay.

Lifecycle publication sequences are assigned only after producer transactions
commit. Consumers must retain the returned opaque cursor rather than compare
timestamps or UUIDs. An expired cursor returns `resync_required`; start a fresh
retained feed rather than assuming erased transitions can be reconstructed.
Installation changes invalidate the old scope, and the explicit read grant is
checked on every request. Audit rows contain identifiers, status and timestamps
only; do not add notification bodies or credentials to replay diagnostics.

Back up before upgrading and apply the single Alembic history through its head.
Lifecycle revision `0037e34ac954` follows `08cca40cdb7f`, preserving main's game
duplicate-review schema. It creates no historical transitions and refuses a
downgrade that would erase populated lifecycle history. Provider secrets and
encrypted cursors use the existing application encryption key; preserve that key
with the deployment's protected configuration.
