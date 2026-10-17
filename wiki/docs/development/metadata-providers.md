# Progressive metadata and Plugin API 1.1.1

The host owns one metadata handler for games, movies, TV and anime. Its core
providers are **hardcoded inside the host application** and invoke their APIs
directly through cancellable asynchronous HTTP. They are not installed plugins,
packages, runtime workers or catalogue downloads. No plugin runtime or manual
provider installation is needed for default game, TV or anime search.

| Built-in provider | Default capability | Credentials |
| --- | --- | --- |
| Steam | Game search, metadata, selected CDN artwork | None for metadata |
| IGDB | Game search, metadata, artwork | System Twitch client ID/secret |
| SteamGridDB | Selected game artwork | System or personal API key |
| TVmaze | TV search, seasons, episodes, airing, artwork | None |
| AniList | Anime search, episodes, airing, franchise/recommendations, artwork | None |
| AniZip | Primary anime episode details and cross-provider IDs | None |
| OMDb | Movie/TV search, metadata and poster | System or personal API key |

Optional GOG, GiantBomb, RetroAchievements, ScreenScraper, HowLongToBeat, Kitsu,
Jikan, TMDB and TVDB implementations live in
[the companion repository](https://github.com/obsoletelabs/unnamed_tracking_app_plugins).
They use the existing verified package, permission, runtime action and gateway
boundaries. Adding an optional provider does not require a host code change.
Both kinds use the same canonical response models, ranking, rolling metadata preloading,
selection, hard deadlines, scoped credentials, health and library persistence.

The native modules are under `features/metadata/builtin`, with `core.py` selecting
the fixed suite and `core_http.py` implementing cancellable pacing and retries.
The older synchronous provider clients remain for account integration/compatibility;
metadata discovery uses the native adapters. AniList's franchise traversal is shared
between the legacy synchronous client and the native asynchronous transport.

The native IGDB adapter reuses Twitch application tokens in memory for each
credential fingerprint, refreshing before their advertised expiry. Concurrent
requests share one refresh while retaining their own deadlines and cancellation.
An IGDB HTTP 401 invalidates only the rejected token and retries once; other errors
retain the normal provider failure and pacing rules. Credential changes use a
separate cache entry. Tokens are never persisted, and old credential entries are
bounded to 32 per backend process.

## Public contract

This is an additive **Plugin API v1 contract version 1.1.1** extension. Packages
using 1.1.0 remain executable; metadata registrations require 1.1.1. The HTTP
gateway envelope remains `api_version: "v1"`. Application routes remain under
`/api`; this patch does not introduce a second runtime.

The exported JSON schemas are authoritative snapshots of the host models:

- [Registration](../assets/plugin-metadata/metadata-registration-v1.schema.json)
- [Request](../assets/plugin-metadata/metadata-request-v1.schema.json)
- [Response](../assets/plugin-metadata/metadata-response-v1.schema.json)

Regenerate with backend dependencies installed:

```bash
PYTHONPATH=src/backend python tools/export_metadata_contract.py wiki/docs/assets/plugin-metadata
```

Copy matching snapshots to the companion's `tools/schemas/` and run its host
contract check. Unknown fields, invalid identifiers, unsafe types, non-finite
scores and oversized collections are rejected. Canonical provider scores use
0–100; existing library score fields retain their 0–10 scale.

### Completion-time categories (API 1.1.13)

`MetadataPatch` accepts three additional optional hour values:
`time_to_beat_extras_hours` (Main story + extras),
`time_to_beat_completionist_hours` (Completionist) and `time_to_beat_all_hours`
(All play styles). `time_to_beat_hours` retains its Main story meaning. The new
values must be finite, nonnegative and at most 99,999.9 hours; storage uses the same
one-decimal precision as Main story. Missing values preserve existing information.
Providers emitting only Main story remain compatible, and earlier v1.1 packages
remain executable. A provider emitting the new fields must target API 1.1.13 so
older hosts reject it before execution rather than rejecting its result later.

Game edits, reads, library export/import, manual field protection, history and
the public game-details gateway carry the additional categories. Metadata refresh
and library enrichment honor the existing Save time to beat setting for all four.
These community estimates never replace the user's personal playtime. Migration
`03bb35a3b692` adds nullable columns after `fed12ab64423`; existing Main story and
playtime values are preserved. The matching SDK/schema and optional HLTB provider
updates belong in the companion plugin repository.

The native IGDB adapter queries its documented
[Game Time To Beat endpoint](https://api-docs.igdb.com/#game-time-to-beat):
`hastily`, `normally` and `completely` map from seconds to Main story, Main + extras
and Completionist. No All play styles average is invented. This bounded auxiliary
lookup reuses native authentication/pacing, and its failure preserves ordinary
IGDB metadata. It never invokes HLTB or another provider.

The host merges useful completion figures from other providers first, preserving
their normal relative priority, then fills missing categories from native IGDB.
Zero/missing figures do not suppress fallback. This authority is independent of
completion order and does not change priority for other metadata fields.

The public outbound header allowlist adds bounded `Accept-Language` and
`X-Auth-Token` for provider initialization. Existing length/count/newline checks,
redirect refusal, response bounds and permission enforcement remain in place.
HLTB obtains and renews its token through the public gateway in the companion
plugin; no provider implementation is copied into the host.

## Registration and permission enforcement

A running plugin calls `metadata_providers.register` with its namespaced ID,
identifier namespace, media types, configuration fields and independent
search/metadata/media/health action IDs. Registration is bound to the current
installation. `metadata_providers.unregister` withdraws it. The host verifies
the declared runtime actions and each corresponding permission.

Required leaves are `metadata_providers.register`, `.search`, `.metadata`,
`.media`, `.health` and `.configuration` as applicable. Network access and
plugin storage require their existing grants separately. Actions recheck
installation integrity, lifecycle and scoped grants before and after invocation;
cached responses also require current authorization. Disable, removal, update
and grant revocation cannot authorize stale work.

Account metadata opt-outs are host preferences, independent of plugin lifecycle,
grants and credential scope. `metadata_providers_enabled` is a bounded map of at
most 256 provider identifiers to JSON booleans; an absent entry means enabled.
The provider catalog includes `account_enabled`, and disabled providers remain
listed so they can be re-enabled. Discovery, health scheduling, fresh invocation
authorization and cached search authorization enforce the account choice. Work
that completes after an opt-out fails the final authorization check.

The existing configuration gateway denies a disabled provider's credential
resolution. Direct plugin UI metadata actions also resolve their current
registration before invoking the runtime. Pass `provider_id` in action values
when an action serves multiple providers. Older actions that omit it still work
when every matching registration is enabled; ambiguous calls cannot bypass an
opt-out. Registration, configuration editing and separate library-sync actions
retain their existing boundaries. This adds no new SDK method or wire-schema
requirement: existing official provider declarations and SDK helpers remain valid.

### Account provider order

`UserScanSettings.provider_order` (search and metadata authority) and
`image_provider_order` (artwork authority) hold registered provider IDs, so core
and optional providers share one order per account. No migration is needed: the
JSON columns keep orders saved before optional providers became plugins, whose
entries are the fixed legacy display names (`IGDB`, `GiantBomb`, `SteamGridDB` and
the other five). Discovery matches an entry by provider ID; only those legacy
names also match a declaration name, and a plugin declaration can never claim
the name of a built-in provider. Providers absent from an order rank
100, after every listed provider; ties use the provider ID. Unsaved accounts use
the model defaults without creating a scan-settings row.

`discover_providers` applies the saved orders on every path, including focus,
refresh, library sync, related metadata and scheduled episode work, unless a
caller passes explicit scan preferences. Metadata patches merge by provider rank.
Artwork sorts by kind, the best artwork rank among the providers that supplied
an asset, the provider's own asset priority, then URL.

`PUT /api/settings/scan` accepts up to 256 unique entries per order. Each must be a
provider ID matching `[a-z0-9][a-z0-9._-]{0,127}` or one of the legacy names
for that order. IDs are not checked against current registrations, so an
uninstalled plugin's position survives a reinstall. Each account can only change
its own order. `GET /api/metadata/providers` adds the effective `priority` and
`artwork_priority` to each entry. The Scan & providers tab builds its lists from
that catalog. It shows account-enabled providers for one media type and moves an
entry relative to its visible neighbour. Orders are sent only after a move.

`provider_last_used` is keyed by provider ID. Selecting a progressive result,
compatibility game search and game metadata refresh record the registered
providers whose identity, metadata or artwork reached the chosen result. The
local library identity is never recorded. Older display-name keys remain
readable as a fallback.

Plugin lifecycle needs no separate registry or hook. A disabled plugin's
providers stay in discovery as `disabled`. A stopped or unreachable runtime
reports `plugin_unavailable`. Uninstalling purges the plugin's grants, so its
registrations leave discovery even though the registration row and encrypted
configuration remain. A reinstall or update registers the same provider ID for
the new installation and keeps its saved position. Account opt-outs keep a
provider's position for re-enabling.

## Three phases

`POST /api/metadata/sessions` returns an owner-scoped session immediately.
`GET /api/metadata/sessions/{id}/events` streams SSE updates with monotonically
increasing IDs; snapshots and `Last-Event-ID` support reconnection. Another user
cannot read, select or cancel the session.

Search providers run concurrently and return lightweight identities. Metadata
starts at five query characters with up to five concurrent fetches, ordered from
the top of the current ranking. Each finished fetch frees a slot for the next
result; slow requests do not hold up a batch. Empty responses and failures also
free slots. Ranking changes cancel unfinished work outside the preload window
and retain completed patches. Changing the query or closing the search cancels
obsolete work; unchanged queries continue down the results without duplicate
requests for already completed operations.
Selection through `POST /api/metadata/sessions/{id}/selection` focuses metadata
and starts artwork. `POST /api/metadata/focus` does the same for an existing
library identity. `DELETE /api/metadata/sessions/{id}` cancels work.

The Vue composable filters retained results immediately using punctuation-free
title/alias comparison, then debounces remote work by 250 ms. Generation,
session and event guards reject stale updates. A late patch fills automatic
values while preserving edits made after selection.

The handler ranks by normalized title/alias and token matches, then stable
provider priority, result position and identity. Shared IDs corroborate merges;
conflicting IDs, media types and known years prevent them. Title-only matches
without corroborating identity do not collapse unrelated editions.

## Deadlines, pages and authority

Interactive operations have an eight-second overall deadline. Background
operations have 25 seconds. These bounds also apply when provider code ignores
cancellation. Late results are discarded. Failure classifications become events
without discarding other providers' results.

Pages share the original deadline, reject repeated cursors and have a bounded
256-page ceiling. Runtime action results keep their 64 KiB limit. Companion
providers use size-aware pages for large episode and relation inventories.
Episode aggregates retain at most 10,000 entries and relation aggregates 500;
an invalid aggregate preserves previously accepted pages and reports failure.

Patches are retained by source. Provider priority and stable provider ID determine
authority, independent of arrival time. Missing values never erase fields.
Known IDs and multilingual titles merge by namespace. Episode fields merge by
season/number, with primary episode sources followed by fallback sources where
fields are missing. Watched state, counters, title locks and personal data stay
at the application persistence boundary.

The same contract handles compatibility search endpoints, explicit refresh,
library sync enrichment, list imports, alternate-title repair, seasons, airing,
episodes, collections, franchise graphs and recommendations. Library models
persist provider IDs so later refreshes can use reliable identity. Account
library/achievement imports retain their separate account integration APIs.

## Configuration, health and caching

Provider-declared configuration supports system, user or both scopes. Admins
can write system values; users can write only their own values. Values are
encrypted with the existing application encryption helper, never returned by
configuration UI reads. Presence flags and safe health classifications are
visible. Native operations receive only the authenticated actor's resolved values; optional
plugin actions obtain those values through the configuration gateway; a user override wins over a system
fallback. The handler does not interpret provider-specific secrets.

Configuration queues immediate validation. A nonblocking monitor discovers new
registrations and validates periodically, defaulting to 1,800 seconds through
`METADATA_HEALTH_INTERVAL_SECONDS` (minimum 60). Validation has its own queue
and does not hold up startup or interactive searches. Optional disabled or
unconfigured providers do not participate. Unavailable providers can be tried
opportunistically. Search shows friendly failures; settings show classifications
and the last validation time.

Clean search results expire two minutes after acquisition, are capped at 256 and
include user, query, media type, options, provider and configuration revision.
Reopening or repeating a search reuses those results without extending their
expiry. Every cache hit still rechecks current core credentials or plugin grants
and lifecycle. Failed responses are retried instead of being cached.
Persisted library identifiers have a separate lifecycle. Debug diagnostics log
provider, phase, elapsed time and bounded outcomes without queries or secrets.

## Deployment and migration

Apply the linear Alembic migration `d8338e79fbbd` after `65acf36995e5`. It adds
registrations, encrypted scoped configuration and game provider IDs; existing
movie/TV/anime identity columns are reused. Keep the application encryption key
stable so stored credentials remain readable.

The seven core providers are ready without installation. Steam supplies keyless
game search; TVmaze and AniList supply keyless TV/anime search. Configure IGDB,
SteamGridDB or OMDb only when those capabilities are wanted. TMDB remains an
optional plugin and may be skipped entirely.

Core credentials have one UI: the familiar provider tiles under Metadata/API,
also shown with **System default** selected in Administration → Server integrations.
Legacy IGDB, SteamGridDB and OMDb system/database/environment keys and personal
SteamGridDB keys are carried forward into missing encrypted scopes. Existing new
configuration rows, including cleared rows, always win. This is a one-time import
per configured scope; rotate imported keys through the new provider controls.
Optional plugins do not receive legacy host keys automatically. Account sync
credentials remain separate, including RetroAchievements achievement/library
credentials and Xbox application credentials. Obsolete admin metadata key forms
have been removed.

## Validation

Form-side lookup uses the existing progressive session/selected candidate. The
shared comparison stages only checked fields and preserves current manual edits,
locks and unchecked choices through late enrichment. A changed form title or
lookup target invalidates stale choices. Provider IDs and TV season identities
are explicit choices; unrelated checked fields do not relink tracking. Current
actor/title enforcement remains authoritative at Save.

`POST /api/metadata/sessions/{session_id}/artwork` previews only a URL currently
offered by that session's selected candidate. Ownership, recorded supplying
provider and current media authorization are checked before pinned public image
fetching and byte/pixel validation. The response is a bounded normalized PNG with
`no-store` and `nosniff`; it never writes a library asset. Source provenance stays
inside the host session. Steam offers its tall cover before a header fallback;
the browser tries only bounded host-verified choices and releases blob URLs.

The existing TV update endpoint accepts an optional bounded `seasons` list for
selected lookup saves. It reuses the higher-numbered-season policy, adds new
seasons in the same transaction and leaves existing season/progress values alone.
No schema migration is needed. These are host form/session changes; Plugin API v1
metadata inputs/outputs, grants and package consumers are unchanged, so optional
provider implementations and SDKs need no release for this adaptation.

Regression suites cover concurrency, hard deadlines, deterministic ranking,
partial patches, identity conflicts, rolling-window changes, selected artwork,
credential scopes, encryption, lifecycle revocation, health and stale UI events.
The companion `tools/check_metadata_live.py` exercises native Steam/SteamGridDB
against real APIs on a disposable development host and prints counts/classifications only.
Unsigned development previews require explicit plugin-manager approval and are
not production distribution artifacts. See its wiki for the live validation
record and providers that remain untested without credentials.

Core regression coverage verifies a seven-provider fresh registry with no runtime
calls, encrypted legacy-key migration, cleared-key preservation, stale credential
rejection, HTTP scope authorization, true request cancellation, identity-only
Steam search, primary episode details and shared franchise traversal. Existing
handler, plugin security, library, migration and frontend suites remain required.
Live checks distinguish native core success from optional-plugin success and
missing credentials; the PR records the exact commands and results.
Real API checks also observed intermittent TVmaze and SteamGridDB timeouts.
Successful results and Steam CDN artwork remained available; response time is
service-dependent. IGDB and OMDb still lack live development credentials, and
TMDB signup was skipped at the user's request.

See [the user workflow and screenshots](../integrations/metadata.md) for the
original source-tile styling, progressive results and selected artwork.
