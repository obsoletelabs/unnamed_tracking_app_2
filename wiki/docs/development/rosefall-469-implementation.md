# Rosefall #469 approved implementation plan

This is an implementation plan, not a description of completed ports. The
[72-row reconciliation](rosefall-469-reconciliation.md) supplies each behavior,
current host comparison, dependency and risk. The [source ledger](rosefall-469-files.md)
links the complete audited source at `2785649b0`. Implementation extends Obsolete
Labs main; source commits/files will not be merged or copied wholesale.

## Accepted decisions

The owner approved the matrix recommendations on 14 October 2026, with these changes:

- **23:** also allow an account to disable providers entirely, including those
  using server credentials. This is distinct from Automatic Search's typing policy.
- **54–55:** exact username first, then email fallback; new/renamed usernames allow
  ASCII letters, digits, underscores, hyphens and dots, with no spaces. Existing
  usernames keep signing in and need not be renamed. Show clear field-level help.
  SQLAlchemy bound parameters remain mandatory; character rules are not a substitute
  for parameterized queries. Track a separate optional display name as follow-up.
- **58:** per-provider **Require verified email**, off by default. When enabled,
  require an explicit boolean `email_verified=true`; reject false, absent or
  malformed claims before linking, creating or updating an account. When off,
  retain existing provider compatibility. Apply the same choice to the environment
  bootstrap provider and named administrator-managed providers.
- **61:** redact the legacy ENV-only database URL in configuration schema; keep its
  connection behavior and precedence unchanged.
- Retain the current implementations for **4, 16, 22, 42, 69, 72**. Use adapted
  alternatives for **3** and **44**: no GET status mutation and no client timestamp
  masquerading as immutable content identity.

Independently authorized CI is merged as #122. Classic presentation, Automatic
Search and the native Classic palette are merged as #123. The palette works with Modern
without changing its presentation. These requests are not Rosefall feature copies.

## Delivery order and branch dependencies

Each package below is a focused PR into main, or several PRs when host/plugin or
data/UI boundaries warrant it. Refresh main before starting each branch. Existing
published histories will be merged forward without force pushes.

1. Account/login/session/API-key and setup safeguards that need no new columns.
2. Optional OIDC verification and collision handling.
3. Safe resource/upload handling and numeric/null validation.
4. Provider opt-outs and default order, then completion and card consistency.
5. Time-to-beat public/data foundations and the separate HLTB provider update.
6. Stable media query/pagination foundations and collection correctness.
7. Shared staged lookup and editor improvements, followed by quick editors.
8. Asset revisions, media trash, scheduler/notification fairness, activity/settings.

The migration history must stay linear. OIDC/time-to-beat migrations will be
generated only after the preceding schema PR is reflected in main, or explicitly
depend on that PR and be checked against its head. Do not invent a competing head
or hide an unrelated PR in the diff. The owner authorized squash merges on
14 October 2026 after each package is complete and all required checks pass.
Until then, PRs remain available for review with current validation and screenshots.

### Current progress

- **A:** merged as [PR #126](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/126).
  Full backend validation passed (2,403 tests, four skipped), frontend validation
  passed (528 tests), and hosted checks passed before the authorized squash merge.
  Modern/Classic account and phone screenshots are included.
- **B:** merged as [PR #127](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/127).
  Opt-in verified email, callback-alias enforcement, collision handling and the
  false-default additive migration are complete. Full backend validation passed
  (2,441 tests, four skipped), frontend validation passed (531 tests), and all hosted
  checks passed before the authorized squash merge. Modern/Classic screenshots
  accompany the provider setting.
- **C1 / 62:** merged as [PR #128](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/128): one pinned public HTTP
  boundary for cached images and game artwork URL downloads, with checked redirects,
  bounded streaming and image validation. The final PR and validation are recorded
  in the [handover](rosefall-469-handover.md).
- **C2 / 63–64:** merged as [PR #129](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/129).
  Extension/content validation, bounded storage names, preserved original names and
  equivalent whole/range response headers are implemented. The generated additive
  migration and shared Modern/Classic UI have regression coverage and screenshots.
  All hosted checks passed before the authorized squash merge: 2,517 backend tests
  (four skipped) and 533 frontend tests. Existing uploads keep their storage paths.
- **D / 30, 65–67:** merged as [PR #130](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/130).
  Import rating normalization, finite restore conversion, required-null PATCH
  handling and shared frontend numeric inputs have regression coverage. Hosted
  validation passed 2,566 backend tests (four skipped) and 566 frontend
  tests. All seven full hosted workflows passed before the authorized squash merge.
  [Modern](../assets/numeric-validation/modern-dark-quick-edit.jpg),
  [Classic](../assets/numeric-validation/classic-dark-quick-edit.jpg),
  [light mode](../assets/numeric-validation/modern-light-quick-edit.jpg) and
  [phone](../assets/numeric-validation/classic-dark-phone-quick-edit.jpg) screenshots
  cover inline validation. The shared quick-editor grid fits the phone dialog.
- **E / 23:** in progress on `feat/metadata-provider-choices`.
  Account provider opt-outs cover builtin/plugin discovery, current invocation,
  cached responses, direct metadata actions and scheduled metadata work. Saved
  credentials and typing choices remain intact. Library-sync actions keep their
  existing permission path. Steam precedes GOG only in unsaved defaults.
  Full local validation passed 2,584 backend tests (four skipped), 569 frontend
  tests, and 16 separate SDK/provider contract tests. Hosted checks are pending.
  Shared settings screenshots cover [Modern](../assets/provider-choices/modern-dark.jpg),
  [Classic dark](../assets/provider-choices/classic-dark.jpg),
  [Classic light](../assets/provider-choices/classic-light.jpg) and
  [phone](../assets/provider-choices/classic-dark-phone.jpg).
- **F–O:** approved; not implemented yet. The package details below describe
  intended work, not completed behavior.

## Concrete packages

Backend paths below are relative to `src/backend/src`; frontend paths are relative
to `src/frontend/src`. Tests belong in their existing respective test trees.
Every package updates the relevant user/developer wiki pages and includes current
screenshots for meaningful UI changes.

### A. Accounts, sessions and setup — 54, 55, 56, 57, 60, 61

Extend `api/routes/auth.py`, `core/auth.py`, `core/session_manager.py`, existing
session-manager routes, `api/routes/setup.py`, `core/config_registry.py` and the
existing user/auth schemas. Put shared username validation at the existing core
authentication boundary, reused by setup, admin creation and profile rename.
OIDC-generated usernames will use the same allowed characters with deterministic
collision suffixes. Login must accept legacy names without applying creation rules.

Use two parameterized lookups: exact username, then normalized email. Retain account
enabled checks, password policy, login throttling, actor audit and cookie handling.
On password change, revoke other active browser sessions through existing session
revocation semantics while keeping the current session. Key creation requires a
browser actor; listing/revoking keys keeps the established ownership/scopes.
After bootstrap, the configuration read route requires an administrator; bootstrap
status and the first-run form remain public where required. Redaction applies even
when a registry field is hidden and ENV-only.

No schema migration or legacy account rename. Add backend HTTP regressions for
username/email collisions, legacy names, rejected characters/SQL-like strings,
clear validation errors, session revocation, browser versus API-key/delegated actors,
first-run/admin/non-admin configuration and secret values. Update
`components/settings/ProfileSection.vue`, admin user controls and generated setup
help through the existing service error handling; do not expose raw database errors.

### B. Optional OIDC verification — 58, 59

Extend `core/oidc.py`, `api/routes/auth_oidc.py`, OIDC administration routes,
`database/models/oidc_settings.py` and the registry/bootstrap path. Named providers
are currently stored in `OidcSettings.providers_json`; the separate `OidcProvider`
table is not used by the active configuration path and does not need a new column.
Store the named-provider choice in its existing JSON configuration and add a
false-default legacy/default setting through one reviewed additive migration.
Existing rows remain compatible. Carry the choice through environment precedence,
configuration resolution, request/response schemas and the frontend OIDC service.

Extend `components/settings/OidcSettingsSection.vue` with a labeled switch and the
exact missing/false behavior. Check the claim before any account mutation. A
subject-bound account cannot adopt another account's email; preserve issuer/subject
binding and return a specific safe login error. Handle a uniqueness race without
an unhandled database 500 or partial linking. No account merging by email.

Tests cover enabled true/false/missing/string claims, disabled compatibility,
named/environment providers, existing subject updates, email collisions and linear
upgrade/default preservation. Update OIDC and environment configuration guides.

### C. Remote assets and uploads — 62, 63, 64

Extend `helpers/remote_images.py`, the current game-asset download helper and
`api/routes/game_assets.py`, plus `helpers/media.py` and `helpers/range_response.py`.
Use the current public-app safe HTTP transport's validated-address principles at
the host asset boundary: URL/port/credential checks, resolved public addresses,
connection pinning, bounded redirects/streaming, image decoding and size bounds.
Keep external fetches out of blocking async request paths and preserve existing
provider health/gateway/permissions. Source URL filtering alone is insufficient.

Validate upload extensions and actual permitted content, store safe filenames while
keeping the original download/display name, and apply nosniff/sandbox headers to
both complete and range responses. Preserve legitimate media playback, preview,
200/206/416 semantics and public plugin document APIs. Before implementing original
filename preservation, discovery found no existing original-name field. The reviewed
additive migration adds nullable original_filename to MediaItem, InboxItem and
GameFileItem. Keep original names separate from safe storage names and editable
titles; do not rewrite existing files or invent missing historical names.
Test private/redirect/rebinding targets, oversized and invalid assets, MIME spoofing,
filenames and range headers through public upload/download routes. Document limits
and any demonstrated compatibility adjustment before changing supported formats.

### D. Numeric and PATCH semantics — 30, 65, 66, 67

Extend `features/imports/lists.py`, `features/imports/yamtrack.py`, restore conversion,
`api/routes/media_common.py` and existing game update/locking helpers. One finite
rating normalizer handles list-source scale conversion, 0–10 bounds and two decimal
places; other numeric fields retain their own precision. Distinguish missing,
required-null, nullable-clear, blank and legitimate zero at the schema boundary.

Reuse a frontend number-input helper in existing game/media/quick forms, rather
than scattering `Number('')` conversions. No schema migration. Test NaN/Infinity,
Decimals, all importer scales, null/zero and locked/actor-protected updates. Preserve
restore transaction guarantees and report invalid entries without silent corruption.

### E. Provider choices and order — 23

Extend `core/preferences.py`, `features/metadata/configuration.py`, provider catalog,
generic handler and invocation resolution, plus the existing
`components/plugins/MetadataProviderSettings.vue` and shared provider settings state.
Persist an account-level bounded provider-ID opt-out map in existing JSON preferences.
Use one generic policy for builtin and optional providers; no provider implementations
or private imports move into the host. Default Steam before GOG for unsaved orders;
preserve every explicitly saved provider order.

The opt-out applies to this account's searches, metadata enrichment/art and scheduled
metadata work for that provider; it must not disable connected-library ownership,
playtime or achievement sync merely because the metadata provider has the same brand.
Server credentials never force an opted-out provider to run. Administrator/provider
availability and granted permissions still constrain users. Automatic Search remains
an additional typing-only restriction. Cancel/ignore pending provider work when
preferences change and expose an understandable disabled reason.

No relational migration or plugin SDK change is expected for host enforcement.
Test builtin/plugin providers, server/personal/no credentials, explicit/automatic/
scheduled operations, selected identity/art, unrelated library sync and actor isolation.
Update provider and Automatic Search guides; inspect actual packaged optional providers.

### F. Completion, folder and card consistency — 1, 2, 3, 5, 10, 12, 13

Extend `api/routes/library_sync.py`, `api/routes/steam_import_steps.py`,
`features/imports/library_games.py` and game update/filesystem boundaries. Centralize
status progression after authoritative ownership/playtime/achievement writes; preserve
manual completion and existing Played/Beaten/Dropped/On Hold/Mastered decisions.
An achievements GET remains read-only. No bulk historical status rewrite.

Folder changes coordinate locked row updates with validated owned paths, collisions,
symlink containment and filesystem rollback. Preserve artwork/files/notes/profiles
and surface a recoverable error on partial failure. No destructive schema change.
Update `components/GameCard.vue` and `composables/useGameLibrary.ts` so successful
card mutations reconcile the shared game and derived counts/filters/preview. Use
existing priority helpers for badges and current detail-section controls for empty
finished resume notes. Test failed save rollback, filters/counts, sync evidence,
ownership, collisions/concurrent upload and asset/profile preservation.

### G. Time-to-beat and provider enrichment — 8, 9, 24, 25

Extend `database/models/game.py`, `api/schemas/game.py`, export/restore,
`features/metadata/locked_fields.py`, public `plugin_api/metadata_contracts.py`,
builtin IGDB adapter and host enrichment/authority handling. Add nullable Main+extras,
Completionist and All styles fields with a reviewed linear migration. Preserve
existing Main story and manual playtime. Public additions are optional and must
remain compatible with plugins supplying only Main story.

In the separate plugin repository, update SDK metadata fields and
`official/metadata-hltb/plugin.py` to implement the initialization/403 retry contract
through the public HTTP gateway, with bounded retries and health/throttling behavior.
Use IGDB's play_times when preferred enrichment supplies no useful value without
putting serial cross-provider orchestration inside IGDB. Extend shared game form,
detail/overview/stats display. Test migration/round trips, locks, partial provider
failures, old SDK payloads and an actual signed `.utp` package; document the additive
contract and separate repository PR dependency.

### H. Library exclusion filters — 11

Extend `utils/libraryFilters.ts`, `composables/useGameLibrary.ts`, persisted presets
and existing filter controls/chips in `views/GameLibrary.vue`. Add exclude statuses,
tags and normalized platform families alongside include filters. No provider requests
or schema migration. Preserve local/offline query identity, selection and clear/reset.
Test combined include/exclude filters, saved legacy presets, keyboard/pressed states
and counts. Document the new chips and preset behavior.

### I. Stable media lists and pagination — 31, 32, 33, 34, 35, 36, 39, 40, 41

Extend `api/routes/media_common.py` and movie/TV/anime list routes with a validated
sort key and stable ID tie-breaks/null handling. Carry sort in existing media services,
`types/mediaLibrary.ts`, `components/library/MediaLibraryView.vue` and cache identity.
Retain real-status counts, five visible buckets and whole versus filtered totals.
No model migration for ordinary sorting. Derive computed progress consistently on
the server where practical; otherwise bound all-page aggregation to that explicit
sort and stop on empty/no-growth results.

Add shared accessible sortable headers, loaded-extent reactivation refresh and
generation guards for stale load-more replies. Retain scroll/offline data and current
mutations. `components/EpisodeList.vue` observes season/list identity instead of every
array replacement. `views/CollectionDetail.vue` removes double decoding; media list
reorder clears the type filter before indexing. Backend tests cover ties, pages and
ownership; frontend tests cover bucket totals, reload extent, stale replies, episode
saves, `%` names and filtered reorder. Document sorting and cache behavior.

### J. Shared staged lookup — 15, 17, 28, 29

Extend `composables/useMetadataSearch.ts`, `utils/useMetadataSearch.ts`, existing
metadata services/selection sessions and protected refresh preview contracts. Add a
reusable form-side comparison model: old/offered fields and artwork, empty editable
fields selected by default, locked fields excluded, checked changes staged locally.
No library write until the editor is saved. Explicit Search/Enter and account/provider
choices remain authoritative. Bind candidates to selected provider identity and
ignore stale revisions or results after unsaved edits change the lookup target.

Retain host title/actor protection (**16**), identity validation and tracking links.
Stage TV season data through current selected enrichment instead of assigning legacy
IDs when any unrelated field was checked. Steam cover fallback is bounded and uses
host-verified asset choices. Tests cover empty/add/edit, locks, stale revisions,
title protection, cancellation, partial provider failures and TV staging. User and
plugin metadata docs describe the shared workflow and untouched protections.

### K. Shared editors and quick editing — 6, 7, 14, 18, 19, 20, 21, 26, 27, 37, 38

Extend `components/GameFormModal.vue`, the current movie/TV/anime editor components,
`MediaFormShell.vue`, `MediaEditDialog.vue`, `GameArtPicker.vue` and existing API
services. Reuse package J's lookup. Present the game hero/eight direct tabs and pinned
actions in one shared editor for Modern/Classic; preserve current compatibility,
relationships, page settings, title locks and plugin slots. Build art tabs lazily
without losing files or remounting plugin state.

Expose favorite/collections/playtime/resume and existing media metadata/personal
fields; use current manual playtime/history APIs. File input wins over URL/provider
art only when explicitly chosen, supports same-file reselection and releases object
URLs. Saving game data and artwork separately reports partial success recoverably.
Shared quick edit adds light game editing and media dates, preserves real statuses
when the visible bucket is unchanged, and opens the full editor without losing edits.
Use current priority/date/title-language helpers in details. No duplicate pages or
presentation-specific business logic. Tests exercise shared save/validation/rollback,
unsaved staged lookup, real-status preservation, art precedence and plugin slots;
screenshots cover both presentations, color modes and narrow screens.

### L. Artwork revalidation — 43, 44

Extend `api/routes/game_assets.py`, default assets and existing frontend art URL
helpers/services. ETag/304 and finite unversioned caching must reflect actual server
content revisions, including uploads/default replacement and normalized variants.
If an immutable URL is introduced, its version identifies the served bytes; a client
timestamp is insufficient. Preserve ownership, missing-file fallback and old URLs.
Test 200/304, two clients, replacements, variants and default art. Coordinate with
package C downloads and K staged assets; document cache headers/revision semantics.

### M. Media trash and statistics — 45, 46, 53

Extend `features/trash/media_trash.py`, existing media trash routes,
`features/trash/sweep.py`, JSON preferences and
`components/settings/MediaTrashSection.vue`. Add per-kind empty trash with explicit
permanent-delete confirmation and opt-in retention never/7/14/30/90 days (never
default). Purge only owned deleted rows, bounded by the current scheduler; preserve
active titles, child cleanup and other accounts. No duplicate cleanup scheduler.

Fix game overview/digest predicates in `api/routes/stats.py`; retain current media
stats/notification exclusions. Test counts, transaction failure, ownership, defaults,
retention cutoffs and activity-history semantics. Document irreversibility and
retention without silently enabling deletion for existing accounts.

### N. Due jobs and anime events — 47, 48, 49, 50, 70

Extend `features/jobs.py`, existing connection scheduling and AniList import,
`features/notifications.py`, scan/event policy and dispatcher boundaries. Select due
accounts before limits, persist failed attempts through current run reporting and
avoid starving later accounts. Register bounded completed-franchise sequel work
with continuation/cursor/deduplication, off by default; classify new seasons versus
movie/OVA/special extras with separate opt-in preferences.

Keep personal schedules, plugin jobs, delivery destinations, trust/privacy and
verification. Frontend calendar/notification controls explain disabled dependencies
without overwriting the saved child choice. Use current JSON preferences and job
storage; add schema only if discovery proves existing persistence insufficient.
Test fairness/failure cadence, defaults, franchise dedupe, routing/ownership and
dependent UI reasons; document events, cadence and manual retry behavior.

### O. Activity, settings and selective copy — 51, 52, 68, 71

Extend `api/routes/media_stats.py`, existing history/stat services,
`components/stats/ActivityHeatmap.vue` and media stats service for a bounded owned
day-detail route/dialog. Agree one local-day/timezone boundary shared with grid
counts; exclude deleted entities and scope all joins. Add month labels and keyboard
equivalents to hover/click. No migration unless the existing activity data cannot
represent an approved detail; do not invent historical events.

Extend `views/Settings.vue`, current settings registry/resolver/navigation and active
plugin sections with keyword search. Reuse current aliases (**69**), hide unavailable
administration and never index secret values. Apply selective clearer wording while
editing affected flows, retaining diagnostics and permission reasons. Tests cover
day boundaries, links, ownership, search keyboard behavior and normal/admin/plugin
visibility. Document both user interactions and plugin/settings integration.

## Verification and progress

Before implementation, reproduce each concrete failure where practical. Add public
behavior regressions, then focused and broader affected suites. Run configured Ruff,
Pylint, mypy, frontend ESLint/Prettier/vue-tsc/Vitest and strict docs checks; ready PRs
and main receive full CI, while branch commits use the merged scope policy. Keep
tests and quality rules intact. Generate and inspect migrations, verify upgrade and
a single head, and test actual package/runtime boundaries when relevant.

Track each package's PR, validated behavior and remaining work here as it lands.
No package is complete merely because its plan exists. Final reconciliation will
distinguish ported/adapted/retained behavior, test results and unresolved limitations.
