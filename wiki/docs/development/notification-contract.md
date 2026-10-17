# Canonical notification contract (1.0.0)

This page is the versioned contract shared by the notification overhaul
([#104](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/104)). It
defines what a notification **event**, durable **story**, canonical **content**,
per-destination **delivery** and physical **attempt** are, how they map onto the
existing [notification core](notification-core.md) records, and which rules
providers and plugins rely on. Trust, destination ownership, consent, grants,
retention and dispatch stay host-owned exactly as described in the notification
core and [plugin notification providers](notification-providers.md).

| Part | Location |
| --- | --- |
| Public wire models, version negotiation and JSON Schema export | `src/plugin_api/notification_story_contracts.py` (exported from `src.plugin_api`) |
| Host mapping onto existing records: transition tables, revision decision, adapters and capability profiles | `src/features/notification_contract.py` |
| Shared conformance cases and reviewed schema snapshots | `src/backend/tests/fixtures/notification_contract/` |
| Contract tests | `tests/test_notification_contract.py`, `tests/test_notification_contract_adapters.py` |

The contract version is `NOTIFICATION_CONTRACT_VERSION = "1.0.0"`. It is
independent of the Plugin API wire version, which stays **1.1.13**: no gateway
operation accepts these documents yet. Plugin-facing operations that adopt them
arrive with the plugin contract work (#115) under a new Plugin API minor version.
Until then every existing producer, provider and plugin keeps working unchanged
through the [compatibility adapters](#compatibility-adapters).

## What is implemented and what adopts it

| Implemented in 1.0.0 | Adopted by |
| --- | --- |
| Typed, strict wire models with JSON Schemas and shared conformance fixtures | SDK/types (#115), producers (#116) |
| Item, delivery and attempt transition tables verified against the current routes and dispatcher | Story persistence (#106), dispatcher (#110), full page (#113) |
| Story revision decision (`decide_revision`) for read/dismissed items | Story persistence (#106) |
| Capability model, discovery profiles for every current provider and the edit/follow-up fallback | Dispatcher (#110), SMTP (#111), Discord (#112), PWA (#114) |
| Renderer contribution model and template rules | Rich rendering (#109), plugin contract (#115) |
| Explicit adapters for every existing producer, record and notification Plugin API surface | All downstream issues until #118 retires adapters |

Nothing in this version adds tables, columns, gateway methods, permissions or
provider behaviour. Registered-source emission derives its unchanged receipt key
through the shared `source_receipt_key` helper.

## Responsibilities

| Concept | Responsibility | Owner | Existing record today | Extended by |
| --- | --- | --- | --- | --- |
| Event | One immutable producer occurrence: type, producer-stable ID, time, optional story reference, grouping hint, urgency request and content. | Producer submits; host authenticates source, recipient and installation. | `NotificationReceipt` (identity, fingerprint, acceptance time) | #106 records story key/phase |
| Story | The durable thing that evolves for one recipient (for example one plugin update) through declared phases and revisions. | Host | None: every `Notification` is an implicit single-item story | #106 |
| Story item | One inbox/history entry presenting a story revision. Read, dismiss and delete apply here. | Host | `Notification` | #106 adds story ID, revision and previous-item link |
| Content | Canonical PRIVATE structured content of one revision. | Producer, validated by host | `Notification.title`, `body`, `poster_url` | #109 persists the canonical document |
| Projection | Host-built copy for less-trusted destinations. | Host | `public_title`/`public_body`, `NotificationDelivery.projection` | Unchanged |
| Destination | Concrete endpoint, revision, privacy, proof, consent and activation. | Host and owner | `NotificationDestination` | Unchanged |
| Provider/renderer | Presents an approved representation within declared capabilities. | Provider; host decides what it receives | Provider adapters and registry | #110 declared capabilities |
| Delivery | Obligation to present one story revision at one destination revision. | Host | `NotificationDelivery` | #110 adds story revision |
| Attempt | One physical send, edit or follow-up operation. | Host | `NotificationDeliveryAttempt` | #110 adds operation and batch ID |

## Identifiers

| Identifier | Chosen by | Scope and uniqueness | Notes |
| --- | --- | --- | --- |
| `event_id` | Producer | Unique per recipient, source and `event_type`; 1–64 opaque characters. | A replay with identical facts is a no-op; changed facts are rejected as a conflict. |
| Receipt key | Host | `NotificationReceipt` unique per user. Plugin sources use `src:<sha256(source:event_type)>:<event_id>`; host producers keep their existing keys such as `plugin-update:<id>:<version>`. | Payload-free and currently never expires. |
| Story key (`story.key`) | Producer | Unique per recipient, source installation (or host source), `event_type` and key; 1–128 printable characters. | Stable for the lifetime of the thing it names. A reinstalled installation cannot revise an earlier installation's stories. |
| Story ID | Host | UUID per story. | Created by #106. |
| Story revision | Host | Integer from 1, incremented for each accepted revision. | Never chosen by producers. |
| Grouping key | Producer hint | Per recipient, source and `event_type`; 1–64 characters. | Drives aggregation (#107) and presentation only. It never merges identities, receipts or stories. Persisted today as `<event_type>:<key>` (sources) or `<kind>:<media_id>` (legacy rows). |
| Notification ID | Host | UUID of a story item. | Existing IDs and read state are preserved. |
| Delivery ID | Host | Unique per (`notification_id`, `destination_id`) today. | #110 extends uniqueness with the story revision. |
| Attempt ID / number | Host | Unique per (`delivery_id`, `number`); bound to a claim token. | Existing. |
| Destination ID / revision | Host | Existing. | A revision change invalidates proof, consent, remote references and unsent work. |
| Batch ID | Host | Groups the attempts of one physical multi-item operation. | Added by #110. |
| Remote message reference | Provider-supplied, host-stored | Per story, destination and destination revision. | Opaque; never exposed to producers. Added by #110. |

## Event document

Schema `notification-canonical-event-v1` (`CanonicalNotificationEvent`):

| Field | Rules |
| --- | --- |
| `contract_version` | Required semantic version accepted by this host (see [versioning](#versioning-and-negotiation)). |
| `event_type` | 3–128 characters, `^[a-z0-9][a-z0-9._-]*$`; plugins stay inside their own namespace. |
| `event_id` | 1–64 characters, opaque producer identity. |
| `occurred_at` | Unix seconds, 0 to 2^53−1. |
| `story` | Optional `{key, phase}`; `phase` is `^[a-z][a-z0-9_]{0,31}$`. Without it the event is a single-item story. |
| `grouping_key` | Optional, 1–64 characters. |
| `urgency` | `normal` (default) or `critical`. A request only. |
| `content` | [Canonical content](#content). Its `expires_at` must follow `occurred_at`. |

Unknown fields are rejected. In particular a producer cannot supply a recipient,
user, destination, provider, installation, source, trust, purpose, severity,
verification, delivery or attempt state, credentials or raw HTML. Those come from
the authenticated gateway context, the host-reviewed type registration and host
records.

Acceptance happens in the caller's transaction without provider I/O, in this order:

1. The gateway authenticates plugin, installation and actor and checks the live grant.
2. The document is validated, then the active type registration supplies trust, purpose and severity.
3. The existing occurrence window and per-account quotas apply.
4. The receipt deduplicates: identical facts are a no-op, changed facts are rejected.
5. The [story decision](#story-revision-decision) runs under the story row lock.
6. Persistence and delivery work use the existing controller and `ensure_deliveries` routing.

Urgency never changes trust, purpose, required trust, destination eligibility or
consent. Host and user policy (#108) decide whether it bypasses timing. Plugins
need the dedicated urgency grant defined by #115 before `critical` is accepted.

## Content

Schema `notification-canonical-content-v1` (`NotificationContent`). It is the
PRIVATE canonical representation of one revision.

| Field | Rules |
| --- | --- |
| `title` | Required single line, 1–500 characters. |
| `summary` | Required multi-line text, 1–10,000 characters. Renderers truncate it deterministically for previews. |
| `blocks` | Up to 32 blocks: `{"type": "text", "text"}` or `{"type": "markdown", "markdown"}`, each 1–10,000 characters. |
| `facts` | Up to 24 `{key, label, value}` with unique keys `^[A-Za-z_][A-Za-z0-9_]{0,31}$` and labels up to 64 characters. |
| `images` | Up to 4 `{role, source, alt}`; role `artwork`, `thumbnail`, `hero` or `icon`; alt text up to 300 characters. |
| `actions` | Up to 5 `{id, label, style, target}` with unique IDs, labels up to 40 characters and style `default`, `primary` or `danger`. |
| `attribution` | Optional `{label, url}` for display. The verified source always comes from host records. |
| `expires_at` | Optional Unix seconds after which the content is stale. |

All displayed text together is limited to 48,000 characters.

Fact values are a closed set: `text` (single line, up to 1,000 characters),
`integer` (±2^53−1, booleans rejected), `decimal` (exact decimal string, up to 15
integer and 6 fractional digits), `money` (non-negative exact `amount` plus an
ISO 4217 `currency`), `timestamp` (Unix seconds, formatted by renderers) and
`boolean`. Binary floating point is never used for displayed facts.

Image sources are `{"kind": "external", "url"}` or
`{"kind": "host_media", "media_type", "media_id"}`. Host media references
resolve only within the recipient's own records and are served by the host's
protected media route; raw provider poster URLs never leave the host. Images are
optional: every renderer must succeed without them and falls back to host artwork.

Action targets are `{"kind": "link", "url"}`, `{"kind": "host_route", "path"}` or
`{"kind": "plugin_action", "action_id", "input"}` with at most 8 scalar inputs.
Plugin actions execute only through a host-authorised route that rechecks the
actor, story ownership, source installation, current grants and action
permission at invocation (#109/#115). Content never contains executable code.

Timestamps are the event's `occurred_at`, the host-assigned revision time and
the content's `expires_at`; domain times such as an airing or sale end are
`timestamp` facts. When canonical content is persisted (#106/#109), the existing
`Notification.expires_at` transport validity becomes the earlier of the content
expiry and the existing purpose lifetime (one day for security, 30 days for
standard notices), so queued work never sends stale content.

### Safety rules

- **Text.** Single-line fields reject C0/C1 control characters, DEL and Unicode
  line/paragraph separators. Multi-line fields additionally allow only line feed
  and tab. Required text cannot be blank.
- **Markdown.** A CommonMark subset. Raw HTML is shown as text, embedded images are
  not rendered (use `images`), and only links satisfying the URL or application
  path rules below become links; others render as text. The rich renderer (#109)
  is the enforcement point.
- **URLs.** `https` only, ASCII (punycode) host names with at least two labels and
  an alphabetic top-level label, no IP literals, no local suffixes (`.local`,
  `.localhost`, `.internal`, `.lan`, `.home.arpa`, `.localdomain`), no
  credentials, default port only, no whitespace or backslashes, up to 2,048
  characters. Validation is syntactic: fetching, proxying, caching, redirects and
  resolution to private addresses remain host policy (#109).
- **Application paths.** Start with a single `/`, contain no `..` segment, `%`,
  `#` or backslash, never target `/api`, and are at most 512 characters. The host
  resolves them with the existing notification URL precedence.
- **Projections.** Content is always PRIVATE canonical content. PUBLIC
  destinations receive only separate host-built projections, such as the existing
  public release projection. There are no field-level "public" flags, and plugin
  content or renderer contributions never reach PUBLIC destinations.

## Story lifecycle

Schema `notification-story-lifecycle-v1` (`StoryLifecycleDeclaration`) declares
the phases of one event type:

- 1–16 phases `{id, label, terminal, severity}` with unique identifiers;
- 1–16 initial phases, all declared;
- up to 64 unique `{from_phase, to_phase}` transitions between declared phases;
- terminal phases have no outgoing transitions;
- every phase is reachable from an initial phase;
- a self-transition (`a → a`) explicitly permits an in-phase content update.

A phase `severity` overrides the type severity for that revision. Trust and
purpose are fixed by the type for the story's lifetime. Events without `story`
use `single_item_lifecycle`: one non-terminal `posted` phase with no transitions,
so they are never revised. Host-reserved purposes (`recovery`, `verification`,
`invitation`, `test`) never join stories; only `standard` and `security`
notifications can (`STORY_PURPOSES`).

Reference lifecycle for plugin updates (also a conformance fixture):

| From | Allowed next phases |
| --- | --- |
| *(new story)* | `available` |
| `available` | `available`, `installed`, `failed`, `resolved` |
| `failed` (severity error) | `failed`, `installed`, `resolved` |
| `installed` (terminal) | — |
| `resolved` (terminal) | — |

A new version is a new story key, for example `update:<plugin>:<version>`.

### Story revision decision

`decide_revision` implements this table. It runs after receipt deduplication
accepted a new event; #106 persists the outcome under the rules below.

| Current story | Requested phase | Head item | Decision |
| --- | --- | --- | --- |
| None | Declared initial phase | — | `create_story` |
| None | Not initial or undeclared | — | `reject_transition` |
| Head phase no longer declared | Any | Any | `reject_transition` |
| Terminal phase | Any | Any | `reject_closed` |
| Non-terminal | Undeclared transition | Any | `reject_transition` |
| Non-terminal | Declared transition | Unread, not dismissed, not deleted, retained and not transport-expired | `update_in_place` |
| Non-terminal | Declared transition | Read, dismissed, deleted, retention-expired or transport-expired | `new_linked_item` |

- **Update in place** keeps the notification ID, replaces its content, increments
  the revision, leaves it unread, sets `event_at` to the revision's occurrence and
  recomputes transport validity. Earlier revision content stays in the story's
  revision history (#106).
- **New linked item** creates a new unread notification linked to the previous
  item. The previous item keeps its content, read/dismissed state and retention.
  The story head moves to the new item.
- **Rejections** fail the acceptance transaction, like a conflicting replay, so no
  receipt is consumed.

Eligibility is evaluated at acceptance time using the existing retention policy
(`inbox_retention_cutoff` reuses `retention_policy`). Marking an item unread again
makes it eligible again; dismissal is not reversible.

Disable, uninstall, unregistration and grant revocation keep their existing
semantics: future work stops, queued work is suppressed and stories, items and
receipts remain in history. A reinstall creates new stories.

### Item operations (read and dismiss)

| Operation | Existing surface | From | To | Replay status |
| --- | --- | --- | --- | --- |
| Read | `POST /api/notifications/{id}/read`, `/read-all` | unread, read | read | `read` |
| Unread | `POST /api/notifications/{id}/unread` | unread, read | unread | `unread` |
| Dismiss | `POST /api/notifications/{id}/dismiss` | unread, read | dismissed | `dismissed` |
| Retention expiry | Existing job loop | unread, read, dismissed | expired | `expired` |
| Delete | `DELETE /api/notifications/{id}` | any except deleted | deleted (content erased, unsent work cancelled) | `deleted` |

State precedence is deleted, retention-expired, dismissed, read, then unread
(`item_state`). Lifecycle replay (Plugin API 1.1.4) uses exactly these statuses,
`created` and the delivery statuses below. Its status list is a pinned public
schema: in-place revisions must not reuse an existing status with a different
meaning, so they are published only after a versioned status is added with the
companion schema (#106/#115).

## Delivery and attempts

Delivery states are the existing dispatcher states (`DELIVERY_TRANSITIONS`).
External work starts `pending`; inbox work is recorded directly as `sent`.

| From | To |
| --- | --- |
| `pending` | `processing`, `suppressed`, `cancelled` |
| `processing` | `processing` (expired lease reclaimed), `sent`, `retry_wait`, `failed_permanent`, `suppressed`, `cancelled` |
| `retry_wait` | `processing`, `suppressed`, `cancelled` |
| `sent`, `failed_permanent`, `suppressed`, `cancelled` | — (terminal) |

Attempts start `processing` and finish `sent`, `failed` or `unknown` (an
expired lease). Errors are safe reason codes only.

Rules for story revisions, batches and edits (implemented by #110):

- A delivery presents one story revision. A later revision creates new delivery
  work keyed by item, destination and revision. Unsent work for a superseded
  revision is `suppressed` with reason `superseded`.
- A batch is one physical operation for one destination revision covering several
  deliveries. Each member delivery records its own attempt with a shared batch
  ID. Chunks respect `max_batch_items` and payload limits and are ordered by
  occurrence time, then notification ID.
- `reconciliation_operation` selects the operation: `send` for the first
  presentation at a destination; `edit` for an in-place revision when the
  provider supports `edit`/`replace` and the remote reference is current for this
  destination revision; otherwise `follow_up`. A new linked item never edits
  content the user already acknowledged. Policy (#108/#111) may decide that a
  revision needs no remote work; then no delivery row is created and diagnostics
  (#117) record the reason.
- Every attempt, including batch members, edits and follow-ups, applies the
  existing dispatch-time revalidation: lease, notification deletion and expiry,
  source installation and grants, preferences and routes, destination revision,
  proof/consent projection, provider registration and installation.
- External delivery stays at least once. Edits and follow-ups are idempotent per
  revision as far as the provider allows; exactly-once remote delivery is not
  claimed.

## Provider capabilities and discovery

Schema `notification-provider-capabilities-v1` (`NotificationProviderCapabilities`):

| Field | Default | Meaning |
| --- | --- | --- |
| `max_batch_items` | 1 | Items per physical message (1–50). |
| `message_updates` | `none` | `edit` an existing message or `replace` it (for example a browser tag). |
| `threaded_follow_ups` | false | Follow-ups can reference the earlier message. |
| `text_formats` | `["plain"]` | `plain` (required), `markdown`, `html_email`. |
| `rich_embeds` | false | Structured embeds or cards. |
| `images` | none | `{max_images, external_urls, proxied_urls}`. |
| `action_buttons` | 0 | Buttons (0–10); requires `links`. |
| `links` | false | Clickable links. |
| `live_page_links` | false | Can carry a host live story link (#109); requires `links`. |
| `max_title_chars`, `max_text_chars`, `max_payload_bytes` | 500, 10,000, 65,536 | Hard limits used for deterministic truncation and chunking. |

Capabilities describe presentation only. Unknown fields are rejected, and
declarations cannot assert trust, verification, sensitive or recovery support,
consent or grants. Critical urgency remains the existing provider feature
(`NotificationProviderFeatures.critical_supported`, built-in adapter flags).

Discovery (`provider_capability_profile`):

1. Built-in providers have fixed host profiles.
2. Plugin registrations use the baseline of their existing transport.
3. A generic `plugin` registration may declare capabilities (#110/#115, new Plugin
   API minor). Only then does it receive canonical content. #110 re-evaluates
   discovery with the current registration at each dispatch.

| Provider | Renderer | Current profile |
| --- | --- | --- |
| `core.inbox` | legacy | Plain text, one host-proxied artwork image, no external links |
| `core.smtp` | legacy | Plain text plus the host link footer, critical supported |
| `core.browser` | legacy | Constant generic preview only, 4,096-byte Web Push payload |
| `legacy` plugin transport | legacy | Title/body DTO, no links |
| `discord_webhook` | legacy | Approved fields as embed, links, 256-character title, 4,096-character text |
| `plugin` (generic) | legacy | Title/body DTO with the host link, critical per declaration |

Every current profile is single-item, non-editable and plain text. Fallbacks
are explicit: Markdown and HTML degrade to plain text, images beyond the limit
drop to host fallback artwork, actions beyond the button limit become links or
are reached through the live page, batches split into chunks and unsupported
edits become follow-ups (`reconciliation_operation`; #110 applies the rest).

## Renderer contributions

Schema `notification-renderer-contribution-v1` (`RendererContribution`) lets a
producer suggest presentation for one of its own event types:

| Field | Rules |
| --- | --- |
| `surface` | `card`, `email`, `chat`, `push` or `text`. |
| `kind` | `fields` (ordered unique references), `markdown_template` or `html_template` (email only). |
| `fields` | References from `title`, `summary`, `blocks`, `facts`, `images`, `actions`, `attribution`, `occurred_at`, `expires_at`, `live_link`. |
| `template` | Up to 20,000 characters using only `{title}`, `{summary}`, `{blocks}`, `{facts}`, `{actions}`, `{attribution}`, `{occurred_at}`, `{expires_at}`, `{live_link}` and `{fact_<key>}`. No format specifications, conversions, attribute or index access. |

The host substitutes values escaped for the template format. HTML templates are
rejected early when they contain scripts, frames, objects, embeds, forms, `base`,
`meta`, `link`, SVG/MathML, inline event handlers, `javascript:`/`vbscript:`/`data:`
attribute URLs, CSS expressions or `@import`; the host allowlist sanitiser (#109)
remains the enforcement point. Contributions never add data, never receive
recipient or destination details and never apply to PUBLIC projections.
Accepting them requires grants that #115 defines; HTML templates additionally
require an explicit high-risk grant.

## Compatibility adapters

Every existing notification gateway method and runtime operation has an explicit
entry in `LEGACY_ADAPTERS`, enforced by a test:

| Surface | Since | Existing documents | Canonical mapping |
| --- | --- | --- | --- |
| `notifications.send` | 1.0.0 | title/body | `content_from_draft`, single-item story |
| `notifications.emit` | 1.1.2 | `NotificationEventEmission` | `emission_event`, unchanged receipt key |
| `notification_sources.register` | 1.1.2 | `NotificationTypeRegistration` | `single_item_lifecycle` |
| `notification_sources.unregister` | 1.1.2 | `event_type` | Unchanged |
| `notification_providers.register` | 1.1.2 | `NotificationProviderRegistration` | `provider_capability_profile` by transport |
| `notification_providers.unregister` | 1.1.2 | `provider_id` | Unchanged |
| Runtime `notification-deliveries` | 1.1.2 | `NotificationDeliveryRepresentation`, `PluginNotificationContent` | `legacy_text` |
| Runtime `notification-layouts` | 1.1.2 | `NotificationFieldLayout` | `legacy_text` |
| `notifications.lifecycle.poll` | 1.1.4 | `NotificationLifecyclePage` | Item operations and delivery transitions |
| `notification_destinations.status` | 1.1.11 | `NotificationDestinationStatusPage` | Unchanged |

Host producers (media, season, sequel and movie rows; game release/sale/price
events; plugin updates; plugin notices; session anomalies; invitation, recovery,
verification and test interpretations) map through `content_from_draft` and
`content_from_notification`. The latter reuses the dispatcher's own
`notification_message` text selection, so `legacy_text` reproduces exactly the
title and body providers receive today for canonical and PUBLIC projections.
Adapters replace control characters with spaces, fall back to `Notification` for
blank titles and never expose raw poster URLs. Deleted items have no content.

Legacy plugin API versions keep their existing boundary: v1.0 packages keep the
limited adapter, and 1.1.0–1.1.13 packages are unaffected. Renderers that
negotiate no canonical version receive `legacy_text`, which appends block text
and `Label: value` fact lines to the summary, truncates at 10,000 characters and
omits producer images and URLs. The host keeps supplying its own notification
link where a DTO has one.

## Host authority

These invariants hold for every adopter:

- Trust is a destination property (PUBLIC/PRIVATE/SECURE). Content, urgency,
  capabilities and renderer contributions never promote it.
- Recipients, destinations, installations and sources come from authenticated
  host context and records, never from documents.
- Destination revision, proof, consent and recovery opt-in are rechecked at
  dispatch for every attempt.
- Grants are checked at acceptance, at dispatch and at action invocation.
- Retention reuses the existing inbox policy; receipts stay separate from content.
- No provider or plugin needs a host-only import: the contract module depends only
  on the public `base_contracts`, the standard library and Pydantic, and the JSON
  Schemas plus conformance cases are sufficient for an independent validator.

## Versioning and negotiation

- Documents carry `contract_version`. The host accepts the same major version
  when it is not newer than the host (`notification_contract_reason`). Malformed,
  other-major and newer versions are rejected; there is no silent downgrade of
  producer documents.
- Within an accepted version validation is strict: unknown fields, block types,
  fact kinds, image sources, action targets, formats and surfaces are rejected.
- Renderers receive `min(declared, host)` within the same major
  (`negotiate_notification_contract`). No declaration or another major selects
  the legacy title/body adapter.
- Additive changes (new optional fields or kinds) are a minor version. Hosts must
  support a minor version before producers may use it, and the host degrades
  content for renderers on older minors. Breaking changes require a new major
  version and new schema identifiers.
- Schema changes update the reviewed snapshots in the same change; companion
  repositories pin those files.

## Downstream obligations

| Issue | Builds on this contract by |
| --- | --- |
| #106 | Persisting stories, revisions and item links with `decide_revision` under transactional locks; story identity scope; receipt boundaries; no reuse of replay statuses. |
| #107 | Grouping by grouping key and declared policy without merging identities; batch ordering and chunking rules. |
| #108 | Urgency as a request constrained by policy, never by trust. |
| #109 | Markdown/HTML/URL enforcement, image proxying, host media resolution, action routes and live story links. |
| #110 | Capability declarations and discovery, per-revision deliveries, batches, remote references and `reconciliation_operation`. |
| #111–#114 | Provider renderers consuming negotiated content or `legacy_text`, and their capability profiles. |
| #115 | Gateway operations, grants and SDK types for these schemas under a new Plugin API minor. |
| #116 | Official producers declaring lifecycles and canonical content. |
| #117 | Safe reason codes for rejected transitions, fallbacks and suppressed revisions. |
| #118 | Retiring adapters only after proving no supported caller depends on them. |

## Conformance and tooling

`tests/fixtures/notification_contract/conformance.json` lists valid and invalid
documents for every schema; each invalid case isolates one rule. The host tests
validate every case, compare published schemas with the reviewed snapshots in
`schemas/`, check the story revision matrix exhaustively, and drive the real
inbox routes, retention job and delivery worker to prove the transition tables
match current behaviour. Companion SDKs should copy the snapshots and conformance
cases and run their own validator against them.

The JSON Schemas are structural. Version compatibility, text, URL and path rules,
uniqueness, total size, lifecycle graph consistency, capability coherence and
template rules on this page are semantic: they are not expressible in the
schemas but each has at least one conformance case, so an independent validator
must implement both layers to pass every case.

Regenerate snapshots after an intentional schema change from `src/backend`:

```bash
python -c "import json, pathlib; from src.plugin_api import notification_contract_schemas as s; [pathlib.Path(f'tests/fixtures/notification_contract/schemas/{n}.schema.json').write_text(json.dumps(v, indent=2, sort_keys=True) + '\n') for n, v in s().items()]"
```
