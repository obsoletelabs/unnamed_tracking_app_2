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

Notification sensitivity and purpose are separate. Session anomaly notices require SECURE destinations. Recovery requires a SECURE **external** destination with explicit recovery support; an internal inbox cannot receive recovery secrets. No production reset flow or external verifier is added by this migration.

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
