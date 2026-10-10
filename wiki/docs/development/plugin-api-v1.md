# Plugin API v1

The host advertises **v1.1.4** while retaining the `v1` wire major. Declared
v1.1.0, v1.1.1 and v1.1.2 contracts remain supported. See
[v1.1 migration](plugin-v1.1-migration.md) for the restricted legacy v1.0
compatibility boundary; omission is treated as v1.0.0.

v1.1.3 adds [bounded game imports](plugin-game-imports.md) under the existing
`games.write` grant. Optional integrations can import their own provider identities
without host-specific code or access to database internals.

v1.1.2 protects notification transport behind core-issued delivery work. Generic
plugin/UI actions cannot authorize Discord delivery. Notification provider
registration and live capability grants remain required; providers receive the
representation approved for the actual destination. See
[notification providers](notification-providers.md) for compatibility restrictions.

Plugin API v1 is the transport-neutral contract foundation for the Plugin Hub.

## Contract surface

The v1 boundary defines:

- application, plugin and installation identity;
- authenticated user request context;
- stable capability names and capability semantic versions;
- user, game, media, safe document, session, and notification-delivery representations;
- structured errors;
- bounded cursor pagination;
- timezone-aware timestamps;
- API version negotiation;
- versioned events, subscriptions and acknowledgements;
- notification and metadata provider coordinator interfaces.
- host-mediated namespaced and privileged backend route declarations.

The contract layer must not expose ORM models, database sessions, environment values, secrets, filesystem paths, or unrestricted application internals.

## Versioning

### Owned library reads and retired data

`games.details.list` (`games.read`) returns owned, non-deleted games with card
metadata, achievement counts and protected artwork URLs. `games.get` returns an
owned game and a page of its achievements, including stable provider/external
identifiers. `games.media.list` (`media.read`) returns owned, non-deleted game
evidence metadata and protected URLs. These methods accept `offset` and a bounded
`limit`, and return `next_offset` and `complete`. They never expose server paths,
credentials or other accounts. The older `games.list` transport remains supported.

`library.legacy.export` requires the separate **`library.legacy.read`** grant.
Large records use bounded base64 chunks (`chunk.offset`, `chunk.next_offset`,
`chunk.total_bytes`, `chunk.sha256`). Retain the row `offset` and send
`chunk_offset` and the snapshot `sha256` until the final chunk advances
`next_offset`. A changed snapshot is rejected so imports can restart safely.
It exports one bounded page from `cards`, `sets`, `bounties`, `bounty_objectives`,
`bounty_evidence`, `bounty_journal_entries` or `bounty_point_transactions` for the
authenticated gateway actor. Child rows are scoped through owned bounties. The
payload's user identity cannot change that scope. It returns `format_version: 1`,
`records`, `next_offset` and `complete`. The retained migration tables are read
through host-owned reflection; retired ORM models and legacy HTTP routes are not
needed. Importing plugins must preserve identifiers and retry without overwriting
edits. Export never changes or deletes the server originals.

`storage.compare_and_swap` (`plugin.storage`) atomically replaces one plugin
storage value if it equals `expected`. Both `expected` and `value` are strings or
`null`; `null` means absent/deleted. It returns `{ "swapped": true/false }` and
preserves namespace restrictions, quota checks and live grant enforcement.
Consumers can safely allocate archive numbers and award a reward once using
retryable comparisons. The runtime serializes competing namespace handles.

The current API version is v1. The contract library provides VersionNegotiationRequest
and VersionNegotiationResponse. Deployed workers send an optional `api_version`
through the existing JSON-line bridge; omission defaults to v1 for older SDKs.
The runtime rejects incompatible versions and advertises supported versions in
health. Success and structured failure responses retain correlation. See the
[deployed platform boundary](plugin-platform.md) for the wire format and limits.

Breaking changes require a new major API version. Additive changes require compatibility review. Event versions are independent of API versions. Capability semantic changes require a capability-version change.

## Request and authorization context

A request contains a request ID, core application ID, plugin identity, installation identity/version, optional user context, and a versioned requested capability.

The DTO does not grant authorization. The gateway must verify installation grants and user scope before servicing the request.

## Events

Events have an API version, event ID/type/version, UTC occurrence timestamp, source, optional user ID and versioned payload. Subscriptions can filter event types and user IDs. The gateway must enforce the events.subscribe grant and prevent cross-user delivery.

## Provider coordinators

Notification and metadata providers return normalized DTOs to core-owned coordinators. Provider selection, authorization, persistence, retries, delivery state and orchestration remain core responsibilities.

## Security

Public DTOs reject unknown fields and are immutable. Error envelopes deliberately exclude stack traces, SQL, secrets and environment values.

The production host/runtime path implements authentication, exact grant checks, runtime isolation, private storage, action dispatch, and structured diagnostics. Contract types still do not grant access by themselves.


## Manifest and dependency model

Plugin API v1 now provides a strict static manifest contract. A manifest declares a stable plugin ID, semantic version, safe entrypoint, SDK/application compatibility ranges, requested capabilities and human-readable permission rationales, dependencies, declarative UI identifiers, namespaced storage quota, and SHA-256 integrity metadata with optional signature/key identifiers.

Manifest validation is deliberately static: it validates data without importing or executing plugin code. Unknown fields, invalid identifiers, malformed semantic versions, duplicate declarations, unsafe entrypoints, and ambiguous legacy migrations are rejected.

Compatibility is evaluated independently for SDK and application versions. An incompatible manifest is classified before activation and is quarantined rather than executed. Manifest version migration is a pure data transformation; it never loads plugin code.

Dependencies support required/optional dependencies, semantic-version constraints, deterministic dependency-first ordering, missing/incompatible dependency rejection, and cycle detection. Dependency resolution occurs before plugin activation.

See [Plugin capability APIs](plugin-capabilities.md) for the current method map and domain-specific limits, and [Plugin backend routes](plugin-backend-routes.md) for authenticated HTTP integration.

Scoped game documents: see [document transport, format policy and security tests](plugin-documents.md).

## Provider media synchronization, background subscriptions and outbound JSON

These are additive **Plugin API v1** operations on the existing gateway. They do
not change package format, SDK, gateway transport or installation identity.

| Operation | Capability | Scope |
| --- | --- | --- |
| `media.sync` | `media.write` | Authenticated caller's existing Movies/TV/Anime domain records |
| `network.request` | `network.outbound` | Bounded JSON GET through the host, outside worker network isolation |
| `tasks.subscribe`, `tasks.unsubscribe` | `tasks.background` | Explicit authenticated UI action's own user; a worker cannot subscribe another user |
| `tasks.subscribers` | `tasks.background` | Plugin's opted-in user IDs; bounded offset/limit, maximum 100 |
| `tasks.request` | `tasks.background` | Delegates only `media.sync`/`media.write` to a subscribed target, checking target background and media grants live |

### Media sync contract

`media.sync` accepts one bounded DTO: `source`, `source_scope`, `external_id`,
`media_type` (`movie`, `tv_show`, `anime`), `title`, optional `genres`,
`runtime_minutes`, `poster_url`, `played`, `in_progress`, `expected_revision`,
`inventory_complete`, and at most 100 `episodes`. Each episode contains
`external_id`, `season` (including zero for specials), `number`, `watched`, optional
`title`, and optional `removed`. Unknown fields and invalid bounds are rejected.

The host derives a deterministic UUID from plugin ID, authenticated user, source,
source scope and external ID. This uses the existing media primary key rather than
introducing another provider database. The plugin stores its external mapping and
returned host ID. Source scope must distinguish server/account namespaces. Names
and category changes do not alter this ID. Identical titles remain distinct.
Upserts serialize by identity using a transaction advisory lock.

A successful response returns `id`, `created`, `status`, `revision`. Revision is a
SHA-256 digest of actual watch status, episode flags and season counts. An existing
item requires its last returned revision. Local watch changes return
`conflict=local_watch_state_changed` and the current revision, without mutation.
A plugin may use that revision only after explicit user resolution. Local deletion
returns `locally_deleted`; a category change returns `category_changed` instead of
creating another record or dropping local notes/references. Do not silently adopt
unrelated existing records by title. Locked metadata, notes, ratings, favorites
and rewatch history remain protected. Legacy `media.import` remains available.

Films map played to WATCHED; unwatched positions imply IN_PROGRESS, otherwise
WATCHLIST. Episodic media writes real episode flags and derives season counters.
Completion requires an explicitly finalized, nonempty complete inventory with
all episodes watched. Until finalization, a partial watched inventory is
IN_PROGRESS. A root object's existence or played flag does not complete a series.
Episode removal is explicit and bounded; it retains unrelated local fields.

### Background consent and lifecycle

Subscriptions are runtime-owned records in a protected `host/tasks/` storage
namespace. Plugin storage operations cannot read/write those records or list their
keys. Registration gets identity from the authenticated action context, not a
payload-supplied user ID. Delegated work uses the existing private HTTP bridge,
then the public gateway checks the target's media grant. Unsubscribed targets,
revoked grants, arbitrary methods, and nested delegation are rejected. Normal
reinstall/update/rollback retains subscription/storage state; purge/uninstall
removes it through the existing lifecycle. Installing a different plugin never
inherits another plugin's subscriptions.

`tasks.request` payload is `{user_id, method: "media.sync", capability:
"media.write", payload: <media DTO>}`. A background grant does not grant media
access or authorize arbitrary impersonation. The target needs both grants.

### Outbound JSON

`network.request` accepts `{url, headers?}` and performs only HTTP(S) GET. It uses
normal TLS verification, an 8-second timeout, no redirects and a 4 MiB response
limit. URLs cannot contain embedded credentials or fragments. Header names are
restricted to Accept, Authorization and X-Emby-Token; count/length and CRLF bounds
are enforced. The generic transport treats those headers as opaque strings and
contains no provider behavior. Responses are `{status: 200, data: <object/array>}`
or a safe `{status, error}` for HTTP/connection failures. Malformed/oversized JSON
is rejected. No arbitrary response headers/bodies are returned on errors.

Network grants remain explicit, high-risk egress authorization; the administrator
chooses appropriate destinations. This operation enables isolated workers to use
HTTP without changing their sandbox policy. It does not proxy playback.

### Existing media actions

Use `frontend.page.extend` with `media.detail.after-header` and an authenticated
`frontend.context.media` action. The existing `external_navigation` action flag
now also applies to contextual buttons: a `redirect_url` is accepted only when
that flag is declared and the target is credential-free HTTP(S). Failed actions
show an unavailable/error message. Native plugins can render normal HTTP links.
Native contributions remount when the authenticated resource context changes, preventing a previous media item's action destination from remaining visible. There is no provider-specific button or media action in core.

## Provider media synchronization, background subscriptions and outbound JSON

These are additive **Plugin API v1** operations on the existing gateway. They do
not change package format, SDK, gateway transport or installation identity.

| Operation | Capability | Scope |
| --- | --- | --- |
| `media.sync` | `media.write` | Authenticated caller's existing Movies/TV/Anime domain records |
| `network.request` | `network.outbound` | Bounded JSON GET/POST through the host, outside worker network isolation |
| `tasks.subscribe`, `tasks.unsubscribe` | `tasks.background` | Explicit authenticated UI action's own user; a worker cannot subscribe another user |
| `tasks.subscribers` | `tasks.background` | Plugin's opted-in user IDs; bounded offset/limit, maximum 100 |
| `tasks.request` | `tasks.background` | Delegates reviewed media sync or notifications to a subscribed target; checks both target grants live |

### Media sync contract

`media.sync` accepts one bounded DTO: `source`, `source_scope`, `external_id`,
`media_type` (`movie`, `tv_show`, `anime`), `title`, optional `genres`,
`runtime_minutes`, `poster_url`, `played`, `in_progress`, `expected_revision`,
`inventory_complete`, and at most 100 `episodes`. Each episode contains
`external_id`, `season` (including zero for specials), `number`, `watched`, optional
`title`, and optional `removed`. Unknown fields and invalid bounds are rejected.

The host derives a deterministic UUID from plugin ID, authenticated user, source,
source scope and external ID. This uses the existing media primary key rather than
introducing another provider database. The plugin stores its external mapping and
returned host ID. Source scope must distinguish server/account namespaces. Names
and category changes do not alter this ID. Identical titles remain distinct.
Upserts serialize by identity using a transaction advisory lock.

A successful response returns `id`, `created`, `status`, `revision`. Revision is a
SHA-256 digest of actual watch status, episode flags and season counts. An existing
item requires its last returned revision. Local watch changes return
`conflict=local_watch_state_changed` and the current revision, without mutation.
A plugin may use that revision only after explicit user resolution. Local deletion
returns `locally_deleted`; a category change returns `category_changed` instead of
creating another record or dropping local notes/references. Do not silently adopt
unrelated existing records by title. Locked metadata, notes, ratings, favorites
and rewatch history remain protected. Legacy `media.import` remains available.

Films map played to WATCHED; unwatched positions imply IN_PROGRESS, otherwise
WATCHLIST. Episodic media writes real episode flags and derives season counters.
Completion requires an explicitly finalized, nonempty complete inventory with
all episodes watched. Until finalization, a partial watched inventory is
IN_PROGRESS. A root object's existence or played flag does not complete a series.
Episode removal is explicit and bounded; it retains unrelated local fields.

### Optional provider enrichment

Adding `sync_mode: "enrich"` selects an additive contract on `media.sync`; omitting
it retains the original deterministic-identity behavior. It adds `provider_ids`
(32 bounded lowercase namespaces), `release_year`, `auto_merge`, `merge_title_year`,
owned `target_id`, `force_watch`, `available`, and `availability_only`. Only known
identity providers match: collection identifiers never identify a film. Anime
TMDB identities distinguish `tmdb.movie` from `tmdb.tv`. A unique provider-ID or
exact trimmed case-insensitive title/year match can enrich an existing record;
ambiguous, contradictory or incomplete matches return owned candidates for review.
Once accepted, the host retains the provider link across renames and replays.

`metadata` permits at most 32 JSON keys and 64 KiB. Supported native fields include
descriptions, credits, dates, studios, countries, languages, tags, age rating and
backdrops; extra JSON remains provider metadata. Artwork has at most eight
credential-free HTTP(S) URLs. Personal ratings fill absent values only. Notes,
locked fields and local ratings survive. `playback` holds bounded position/runtime
ticks, percentage, play count and last-played Unix timestamp. `episode_progress`
holds at most 100 such records keyed by remote episode ID per request. `history`
holds at most 100 stable event IDs, played timestamps, optional episode identities
and durations, with `reported_session` or `observed_last_played` provenance.
Counts alone must never be presented as individual sessions.

Domain changes, links, snapshots and sessions commit together under a user-scoped
transaction lock. Repeated requests do not duplicate records or events. Metadata
refreshes preserve local watch edits; a later conflicting remote watch change
requires review and an explicit `force_watch` decision. Episode mappings retain
native IDs across provider renumbering. Other accepted provider snapshots contribute
watched flags without treating provider updates as local edits. `availability_only`
updates an existing owned link without altering native records or watch history.

The authenticated native `/api/media/provider-state` endpoint exposes owned provider
snapshots and viewing history with `media_type`, `media_id`, `offset`, `limit`
(maximum 100) and `has_more`; another user's media returns 404. Plugins still use
the gateway and their granted media capability, never this database directly.

### Background consent and lifecycle

Subscriptions are runtime-owned records in a protected `host/tasks/` storage
namespace. Plugin storage operations cannot read/write those records or list their
keys. Registration gets identity from the authenticated action context, not a
payload-supplied user ID. Delegated work uses the existing private HTTP bridge,
then the public gateway checks the target's media grant. Unsubscribed targets,
revoked grants, arbitrary methods, and nested delegation are rejected. Normal
reinstall/update/rollback retains subscription/storage state; purge/uninstall
removes it through the existing lifecycle. Installing a different plugin never
inherits another plugin's subscriptions.

`tasks.request` payload is `{user_id, method: "media.sync", capability:
"media.write", payload: <media DTO>}`. A background grant does not grant media
access or authorize arbitrary impersonation. The target needs both grants.
`notifications.send`/`notifications.send` is the other reviewed delegation pair;
optional notification denial must not break sync. No other methods can be delegated.

### Outbound JSON

`network.request` accepts `{url, headers?, method?: "GET"|"POST", body?: object}`. It uses
normal TLS verification, an 8-second timeout, no redirects and a 4 MiB response
limit. URLs cannot contain embedded credentials or fragments. Header names are
restricted to Accept, Authorization and X-Emby-Token; count/length and CRLF bounds
are enforced. The generic transport treats those headers as opaque strings and
contains no provider behavior. POST JSON bodies are bounded to 64 KiB; other methods
and non-object bodies are rejected. Responses preserve successful HTTP status and
return `{status, data: <object/array/boolean>}`
or a safe `{status, error}` for HTTP/connection failures. Malformed/oversized JSON
is rejected. No arbitrary response headers/bodies are returned on errors.
Numeric Retry-After is returned only as bounded `retry_after_seconds` (maximum
3600). Untrusted TLS certificates return `code: "certificate_untrusted"`; the host
must trust the CA before connection. The transport never offers a TLS bypass.

Network grants remain explicit, high-risk egress authorization; the administrator
chooses appropriate destinations. This operation enables isolated workers to use
HTTP without changing their sandbox policy. It does not proxy playback.

### Existing media actions

Use `frontend.page.extend` with `media.detail.after-header` and an authenticated
`frontend.context.media` action. The existing `external_navigation` action flag
now also applies to contextual buttons: a `redirect_url` is accepted only when
that flag is declared and the target is credential-free HTTP(S). Failed actions
show an unavailable/error message. Native plugins can render normal HTTP links.
Native contributions remount when the authenticated resource context changes, preventing a previous media item's action destination from remaining visible. There is no provider-specific button or media action in core.

Native activation context includes the installed `version` and the existing safe
Vue helpers plus `onBeforeUnmount`, allowing component polling to stop on navigation
as well as plugin-level `onCleanup`. Privileged native frontend consent still applies.

## Metadata provider extension (1.1.1)

Search and refresh use the hardcoded core providers and optional installed provider plugins
through one shared metadata handler. Core search does not require the plugin runtime.
Provider configuration and health appear in the host's metadata settings.
See [the progressive metadata contract](../development/metadata-providers.md) for
phase separation, scoped credential migration, deadlines and persistence behavior.

### Notification event sources

Contract 1.1.2 adds `notification_sources.register`, `notification_sources.unregister` and `notifications.emit` through the existing gateway/capability system. Public DTOs `NotificationTypeRegistration` and `NotificationEventEmission` are exported from `src.plugin_api`. Namespaced declarations are host-interpreted, recipient scope comes from the authenticated action/subscribed background user, and providers remain separate. Sensitive content additionally requires the explicit critical `notifications.sensitive` grant, which broad notification/full-API grants do not imply. See [notification core](notification-core.md#registered-plugin-notification-sources-contract-112) for limits, routing and retained lifecycle semantics. Existing `notifications.send` callers remain supported.

## Notification lifecycle replay (1.1.4)

`notifications.lifecycle.poll` requires a separate explicit `notifications.lifecycle.read` grant. Neither `notifications`, `events.subscribe` nor `api.full` implies it. Requests contain only an optional opaque cursor and a page limit (1–200). The host supplies the authenticated user and current installation; caller-selected owners, installations, providers or destinations are rejected.

The feed contains IDs, UTC occurrence timestamps and status changes only. A source receives its own notification creation/read/unread/dismissal/deletion/inbox-expiry changes. A provider receives only its own delivery states: pending, processing, sent, retry_wait, failed_permanent, suppressed and cancelled. Source permission does not expose deliveries to other providers. Entries contain no message text, type facts, destination IDs, addresses, errors, credentials or recovery authority. Current installation identity and the live grant are checked by the existing gateway, so revocation immediately stops reads and reinstall cannot claim an earlier installation's history.

Responses contain `events`, an authenticated encrypted `cursor`, `has_more` and `resync_required`. Persist the returned cursor only after processing the page. Event UUIDs remain stable across replay. A null cursor starts at retained history. When `resync_required` is true, discard stale local lifecycle assumptions and use the returned cursor to restart within retained history; expired history cannot be reconstructed. This is a change feed, not a full current-state snapshot. Invalid, forged or differently scoped cursors fail validation.

Core transitions and metadata outbox writes commit atomically. The existing job loop publishes committed changes in bounded batches, using a transactional counter under a short stream lock. Producers never take that stream lock; allocation order in the unpublished outbox is not a replay cursor. A transaction that commits late receives a later published position, avoiding skipped events. Delivery retries and global state remain core-owned. Polls may lag one job tick; the API makes no transport calls. Failed/rolled-back transactions publish nothing.

`NOTIFICATION_AUDIT_RETENTION_DAYS` configures this independent audit history, default 90 (1–3650). Inbox retention and dedupe receipts remain separate. Audit metadata survives notice deletion and plugin removal, but account deletion removes that account's records. Migration `0037e34ac954` adds only outbox/audit/stream tables after `42bb6ebaa05e`, with no fabricated historical changes. Adoption preserves existing data; downgrade refuses populated history or an advanced cursor stream.