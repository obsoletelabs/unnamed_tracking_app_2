# Progressive metadata and Plugin API 1.1.1

The host owns one metadata handler for games, movies, TV and anime. Provider
implementations live in [the companion plugin repository](https://github.com/obsoletelabs/unnamed_tracking_app_plugins).
They use the existing package, permission, runtime action and gateway boundaries.
Provider installation does not require adding a provider name to host code.

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

## Three phases

`POST /api/metadata/sessions` returns an owner-scoped session immediately.
`GET /api/metadata/sessions/{id}/events` streams SSE updates with monotonically
increasing IDs; snapshots and `Last-Event-ID` support reconnection. Another user
cannot read, select or cancel the session.

Search providers run concurrently and return lightweight identities. Metadata
starts at five query characters for the current top three. Ranking changes
cancel unfinished work on demoted candidates and retain completed patches.
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
visible. Provider actions obtain only the authenticated actor's effective
values through the configuration gateway; a user override wins over a system
fallback. The handler does not interpret provider-specific secrets.

Configuration queues immediate validation. A nonblocking monitor discovers new
registrations and validates periodically, defaulting to 1,800 seconds through
`METADATA_HEALTH_INTERVAL_SECONDS` (minimum 60). Validation has its own queue
and does not hold up startup or interactive searches. Optional disabled or
unconfigured providers do not participate. Unavailable providers can be tried
opportunistically. Search shows friendly failures; settings show classifications
and the last validation time.

Short-lived search cache entries expire after 30 seconds, are capped at 256 and
include user, query, media type, options, provider and configuration revision.
Persisted library identifiers have a separate lifecycle. Debug diagnostics log
provider, phase, elapsed time and bounded outcomes without queries or secrets.

## Deployment and migration

Apply the linear Alembic migration `d8338e79fbbd` after `65acf36995e5`. It adds
registrations, encrypted scoped configuration and game provider IDs; existing
movie/TV/anime identity columns are reused. Keep the application encryption key
stable so stored credentials remain readable.

Install the desired signed official packages through the plugin manager, review
permissions, enable them and configure credentials in the provider panel. Move
metadata keys from legacy integration settings to the matching plugin fields;
keys are not silently copied across permission boundaries. Existing account
sync credentials remain in account integrations. TMDB is optional; installations
can use TVmaze for TV and the public anime providers without signing up for it.

## Validation

Regression suites cover concurrency, hard deadlines, deterministic ranking,
partial patches, identity conflicts, top-three changes, selected artwork,
credential scopes, encryption, lifecycle revocation, health and stale UI events.
The companion `tools/check_metadata_live.py` exercises installed packages against
real APIs on a disposable development host and prints counts/classifications only.
Unsigned development previews require explicit plugin-manager approval and are
not production distribution artifacts. See its wiki for the live validation
record and providers that remain untested without credentials.

Development validation on 2026-10-07 passed the complete backend suite (1,527
tests, two skips), followed by five focused tests including the new achievement
redirect regression. The frontend passed 294 tests, lint, formatting, type
checking and a production build. Runtime tests passed 145 cases. Backend mypy
checked 257 files and Pylint scored 10/10. A fresh disposable database upgraded
through the parent and new migration, downgraded to the parent and re-upgraded
successfully. Both wiki trees build in strict mode. The companion's validation
record distinguishes live API success from fixture coverage and missing credentials.

See [the user workflow and screenshots](../integrations/metadata.md) for the
original source-tile styling, progressive results and selected artwork.
