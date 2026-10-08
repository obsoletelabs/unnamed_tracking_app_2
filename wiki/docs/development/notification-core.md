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

The dispatcher independently checks transport eligibility and resolves current built-in configuration again before I/O. Plaintext can carry ordinary notifications, with a warning. Security, recovery and verification require TLS except for development mode or a literal local SMTP server IP (loopback/RFC1918/ULA). `localhost` or DNS resolution alone is not proof of the local exception. STARTTLS and implicit TLS use verified SSL contexts without plaintext fallback.

Revision `efc50aae7b57` adds email configuration, encrypted endpoints, challenge records and normal-default urgency fields after `22ff87d4d01c`. It does not alter unrelated game-note tables. Downgrade refuses active email destinations or unsent SMTP work. `tests/test_notification_email.py` exercises real isolated loopback SMTP delivery plus ownership, routing, transport exceptions, proof expiry/replay/revision/revocation/limits, safe DTOs and transient versus permanent failures.
