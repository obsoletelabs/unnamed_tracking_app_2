# Rosefall #469 reconciliation — review selections

This review compares Rosefall #469 at `2785649b061f59bf6b567b5cb17a0bded74a56e9`
with Obsolete Labs `main` at `2246df175fbb556b76d5a600c32ae33ae524a567`, including
the completed #121 host work. The repositories remain separate. No Rosefall
commit has been merged or cherry-picked, and no feature below has been selected
for implementation yet.

The independently authorized Classic/Automatic Search work is in [#123](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/123).
CI changes are in [#122](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/122).
Those requests do not constitute approval of the feature ports below.

## Review choices

For each number, choose **PORT**, **DO NOT PORT**, **ALREADY COVERED / LEAVE ALONE**,
or **DECIDE LATER**. PORT means implement the capability in the current host
architecture; it does not mean copy the source implementation. Recommendations
are proposals, not decisions. Dependencies are named by feature number.

Statuses describe availability and implementation together: MISSING or PARTIALLY
PRESENT can require a COMPATIBLE PORT or ARCHITECTURAL PORT. SUPERSEDED means retain
the current solution. REGRESSION RISK means reject the direct source approach.
Complexity is Low/Medium/High, rather than an elapsed-time promise.

## Games, completion and library

| # | Feature / Rosefall behavior | Obsolete main behavior | Status / recommendation | Complexity | Dependencies and risks |
| --- | --- | --- | --- | --- | --- |
| 1 | Advance existing synced games from Backlog to Played when playtime or achievements arrive; to Mastered when every achievement is unlocked. | Initial import infers status; existing titles do not get the general ongoing progression. Host has richer manual achievement evidence and compatibility tracking. | PARTIALLY PRESENT / ARCHITECTURAL PORT: add one shared progression policy after authoritative sync. | Medium | Preserve explicit Beaten, Dropped, On Hold and Mastered; do not treat local overrides as provider evidence. Extend library sync and imported-game boundaries, not plugins' private code. |
| 2 | Move an owned wishlisted game into Backlog/Played/Mastered during sync. | Owned-library matching exists, but lacks #469's general wishlisted-status advancement. | PARTIALLY PRESENT / ADAPT with 1. | Medium | Requires authoritative ownership evidence; wishlist-only import must not imply purchase. Preserve platform/Playnite identities. |
| 3 | Heal status while GETting achievements, then reread the game in its detail page. | Achievement reads do not use this source repair policy. | REGRESSION RISK / replace with event-driven repair from 1, not GET mutation. | Medium | Read routes, integration scopes and race handling must remain predictable; offer a bounded repair for historical rows separately if wanted. |
| 4 | Never replace RetroAchievements/PSN achievements with an empty result. | Already distinguishes failed lookup (`None`) from a successful empty snapshot, preserves stored rows on failure and reports unavailable titles. | SUPERSEDED / LEAVE ALONE. | Low | Source's blanket empty check would prevent legitimate successful empty snapshots. Retain host error semantics and associated media/evidence. |
| 5 | Rename a game's folder on disk when folder_location changes; undo movement if the database rejects it. | Folder identifiers are validated and uniqueness errors are handled; the ordinary edit does not provide the source filesystem move/rollback workflow. | PARTIALLY PRESENT / ARCHITECTURAL PORT. | High | Game asset/file/profile paths, row locking, collisions, symlinks, rollback on every failure, and concurrent uploads. Never rename another user's directory. |
| 6 | Edit Favorite and collection memberships directly in the full game form. | These are stored and editable through existing library controls; the full form preserves saved values but does not offer the complete source controls. | PARTIALLY PRESENT / extend shared editor. | Medium | 14; preserve smart collection behavior and library state synchronization. |
| 7 | Edit total playtime and resume note in the full game form. | Existing game detail/playtime APIs and notes remain available; source's combined full-form controls are missing. | PARTIALLY PRESENT / ADAPT. | Medium | 14; use existing manual tracking APIs/history. Source saves the game then playtime/art separately, so partial failures need recoverable reporting. |
| 8 | Persist Main story + extras, Completionist and All play styles alongside Main story time. | Model/public MetadataPatch supports Main story only. Optional HLTB provider also emits only that field. | MISSING / ARCHITECTURAL PORT. | High | New linear migration, schemas/types, locks/history/export/restore and public plugin contract; independent SDK/HLTB plugin update. No plugin implementation in host. |
| 9 | Show all time-to-beat categories in game details; Main story in overview and Completionist in stats. | Main story exists in current metadata/game information; additional categories are unavailable. | PARTIALLY PRESENT / ADAPT. | Medium | 8; hide unavailable values, preserve personal playtime and current widgets/layouts. |
| 10 | Hide an empty resume-note block for finished games, retaining written notes and active editing. | Resume notes and configurable game page sections already exist; this source visibility rule is additional. | COMPATIBLE PORT / optional shared visibility refinement. | Low | Respect game-page overrides and keyboard access to creating a note. |
| 11 | Exclude selected statuses, tags or platforms, with saved presets and removable “Not …” chips. | Include filters/presets exist; the source exclusion filters are absent. | MISSING / extend current filter composable/presets. | Medium | No provider calls; support active query/local cache, normalized platform families, clear/reset and accessible pressed states. |
| 12 | Card status/favorite changes immediately update the parent library's filters, counts and preview. | Card watches already follow parent edits, but its own status/favorite saves update local refs only; the returned game is discarded and no parent event updates filters/counts/preview. Other library mutations already update shared state. | PARTIALLY PRESENT / reconcile successful card mutations through the current library composable. | Medium | Preserve current cache/offline behavior and rollback on failed saves; regression test card mutation against filtered list/preview. |
| 13 | Display priority on cards and hide inactive/finished priority consistently. | Priority normalization, sorting and library preview labels exist; the game card itself has no priority badge. | PARTIALLY PRESENT / add the card badge through existing priority helpers. | Low | Preserve current priority semantics, completion badges, layouts and user controls. |

## Editors, metadata, search and artwork selection

| # | Feature / Rosefall behavior | Obsolete main behavior | Status / recommendation | Complexity | Dependencies and risks |
| --- | --- | --- | --- | --- | --- |
| 14 | Shared game form with hero artwork/title, eight direct tabs, fixed-height body and pinned actions; no add wizard. | Shared UiModal game editor has quick/full form, a Find step and current host fields/contributions. | PARTIALLY PRESENT / ARCHITECTURAL PORT of the form experience. | High | 6–9, 15, 18–19; preserve multiplayer compatibility, title protection, relationships, page settings and plugin UI slots. One editor for Modern and Classic. |
| 15 | “Look up metadata” panel in add/edit: choose a result, inspect old/new fields/art, tick changes, then Apply. Empty fields selected by default on edit. | Progressive sessions, selection enrichment, locks and protected refresh previews exist, but this field-by-field form-side panel is missing. | PARTIALLY PRESENT / ARCHITECTURAL PORT. | High | Extend the existing composable/session and preview services. Honor Automatic Search, current provider identity, locks, unsaved edits and stale revisions. Stage changes without saving the library row. |
| 16 | Avoid replacing deliberately changed metadata during lookup. | Host already enforces title protection by actor and field locking across APIs/imports/refreshes. | SUPERSEDED / retain host enforcement. | Low | 15 must extend the protected contract; source game lookup does not provide equivalent host title/actor protections. |
| 17 | Try tall Steam cover, then header fallback, choosing an image that actually loads. | Existing host uses canonical Steam URLs, selected artwork operations and normalized downloads; #469 adds browser load verification to its lookup. | PARTIALLY PRESENT / ADAPT fallback behavior. | Medium | 15, 62; bounded failures and host-verified assets. Do not create uncontrolled browser provider requests or select another game by title. |
| 18 | Manual artwork file wins over URL/provider image; same-file reselection works; show current versus offered art. | Upload/URL/selected-art APIs exist; source consolidates these controls in the redesigned form. | PARTIALLY PRESENT / shared control refinement. | Medium | 14–15, 62; revoke object URLs, preserve saved art until explicit replacement and report game-save/art-save partial outcomes. |
| 19 | Build artwork tab lazily and keep chosen files/candidates when switching tabs. | Current editor retains its own form state and provider session; source changes tab lifecycle. | COMPATIBLE PORT / preserve state in shared editor. | Low | 14, 18; do not remount plugin contributions or lose pending asset tasks. |
| 20 | Light game edit for status/platform/rating/art, with Edit more opening the full editor. | Quick add exists; library edit opens the current full editor. | MISSING / shared quick edit. | Medium | 14, 18; use existing update/asset APIs and form validation; preserve protected fields and saved data. |
| 21 | Artwork picker labels an existing cover “Current” versus a search cover “Search”. | Existing picker/current cover behavior is available; source adds clearer labeling for edit contexts. | COMPATIBLE PORT / small labeling adaptation. | Low | 18, 20; preserve selected-entity artwork provenance. |
| 22 | Rank exact titles, then prefix matches, then all query words; use stable provider/result tie breaks. | Current progressive identity/ranking pipeline already has exact/prefix/token scoring, merging and authority ordering. | SUPERSEDED / retain current ranking. | Low | Add only a demonstrated ranking regression; source synchronous result sorting would discard progressive identity behavior. |
| 23 | Default Steam before GOG, retaining user provider order. | Default provider order still has GOG before Steam; host adapters use that order for authority. | COMPATIBLE PORT / change new/default ordering only if selected. | Low | Preserve explicit saved order; do not migrate everyone's provider preferences silently. Independent of 22. |
| 24 | HLTB initialization token, browser headers and bounded retry after 403. | Active optional HLTB plugin uses a best-effort POST without the new handshake; the legacy host HLTB module is not the active optional provider. | MISSING / ARCHITECTURAL PORT in the plugin repository. | High | Public HTTP gateway permissions, throttling/health, retry bounds and actual provider smoke tests. Do not copy HLTB into host core. |
| 25 | IGDB play_times fallback if HLTB is unavailable or supplies no useful time. | Current builtin IGDB/public metadata handler does not implement this source fallback. | MISSING / ADAPT builtin enrichment and authority. | Medium | 8 for all categories; preserve independent provider failures and user authority/locks. Avoid serial cross-provider calls inside IGDB. |

## Movies, TV, anime and collections

| # | Feature / Rosefall behavior | Obsolete main behavior | Status / recommendation | Complexity | Dependencies and risks |
| --- | --- | --- | --- | --- | --- |
| 26 | Consolidate movie/TV/anime forms into one shared tabbed shell with pinned actions and lookup from every tab. | Individual editors share host abstractions but remain separate components and have additional protection/plugin behavior. | PARTIALLY PRESENT / ARCHITECTURAL PORT of reusable form sections. | High | 28–30; preserve media-specific progress, seasons, deletion, locks and plugin contributions; avoid one giant untyped component. |
| 27 | Expose existing rewatch/date, country/language, source, age rating, feature/tag, subrating, format and provider-ID fields in forms. | Most storage/API capabilities already exist; current editors expose a subset. | PARTIALLY PRESENT / field-by-field shared form additions. | Medium | 26, 30; distinguish personal fields from provider metadata and retain immutable identity/actor rules. |
| 28 | Media lookup compares selected old/new metadata and art, skips locked fields and applies checked changes locally. | Progressive media selection and locks exist; the checked field preview is missing. | PARTIALLY PRESENT / ARCHITECTURAL PORT. | High | Reuse 15's shared preview model and generic session, rather than another timer/search client. |
| 29 | Repair selected provider IDs and stage TV seasons when applying a lookup. | Current provider identity map and selected enrichment are authoritative; source directly assigns TVmaze/MAL/AniList legacy IDs. | PARTIALLY PRESENT / ADAPT through public identity handling. | Medium | 28; never change a library entity identity merely because one unrelated field was checked. Preserve validated IDs and tracking associations. |
| 30 | Clearing number inputs yields null; retain valid zero values and reject non-finite values. | Numeric normalization exists in some host workflows; forms still use heterogeneous conversion. | PARTIALLY PRESENT / shared normalization regression fixes. | Medium | 27, 37, 65–66; optional versus required fields and legitimate zeros must stay distinct. |
| 31 | Server sorting across all pages: score/rank/name/date/year with stable ID tie breaks. | Media API orders by sort_title; sorting the currently loaded host pages cannot ensure globally correct pagination. | MISSING / ARCHITECTURAL PORT. | High | LibraryQuery/filter API, SQL sort/null ordering and stable ties, all three services and cache keys. Preserve exact ranks/counts and user scoping. |
| 32 | Clickable media column sort headers with consistent active-column highlights. | Game list sorting exists; media headers lack the source's complete shared affordance. | PARTIALLY PRESENT / shared sortable header controls. | Low | 31; keyboard activation, aria-sort and theme tokens. |
| 33 | Sum real media statuses into five visible buckets so All/Completed counts remain accurate. | Server exposes real-status counts; UI constructs displayed buckets and totals. Source adds explicit bucket summation tests. | PARTIALLY PRESENT / targeted count parity. | Medium | 31 and current filter semantics; distinguish whole-library counts from filtered result total. |
| 34 | On return, refresh the loaded extent without shrinking to page one; leave data fresh for two minutes. | Current application already keeps libraries alive and has newer cache/offline workflows; media reactivation still needs source behavior reconciliation. | PARTIALLY PRESENT / ADAPT loaded-extent refresh. | Medium | 31; preserve search/filter query identity, scroll position, offline results and mutation reconciliation. |
| 35 | Ignore an old load-more response after search/filter changes. | Request invalidation exists in several current views; source closes this specific pagination race. | PARTIALLY PRESENT / verify and add missing generation guards. | Low | 31, 34; stale replies must not reset current loading/error state. |
| 36 | Load remaining media pages in background for computed progress sorting, stopping on empty/no-growth pages. | Current media progress is locally computed; globally correct sorted paging is not available. | PARTIALLY PRESENT / ADAPT bounded aggregation or server progress sort. | Medium | 31; do not fetch every library automatically for ordinary sorts or create unbounded loops. |
| 37 | Light media edit includes start/end dates and preserves Favorite/Rewatch real status when its visible bucket was unchanged. | Quick edit exists but lacks the source's complete dates/bucket-preservation behavior. | PARTIALLY PRESENT / shared quick-edit fix. | Medium | 30; no silent Completed→Watched conversion, exact progress/history and saving/error states. |
| 38 | Show priority and formatted start/finish dates consistently on media details. | Priority/date data and helpers exist; source adjusts detail presentation. | PARTIALLY PRESENT / targeted display changes. | Low | Preserve title-language choice, current layout and user page controls. |
| 39 | Episode pager does not jump after a watched/rating/note save replaces the array. | Pager watches array identity, which can change on each returned show object. | COMPATIBLE PORT / fix watcher identity. | Low | Observe season/list identity and length; preserve legitimate resets when the season/order actually changes. |
| 40 | Collections containing `%` work because router parameters are not decoded twice. | CollectionDetail still calls decodeURIComponent on the already-decoded route parameter. | COMPATIBLE PORT / remove duplicate decoding. | Low | Regression names: `50% Off`, Unicode and encoded slash behavior; retain backend ownership/validation. |
| 41 | Entering collection reorder clears media-type filtering so nudge indices refer to the displayed full list. | Reorder operates on the full underlying list; onEnter resets sorting but not the type filter. | COMPATIBLE PORT / prevent filtered-index mismatch. | Low | Preserve stored manual order and accessible keyboard controls. |
| 42 | Collections/list terminology and clearer destructive-action text. | Current host already uses collections and preserves aliases; most source changes are wording, not new collection functionality. | ALREADY PRESENT / LEAVE ALONE except selected copy refinement. | Low | No route/model renaming or removal of compatibility aliases. |

## Caching, trash, activity, jobs and notifications

| # | Feature / Rosefall behavior | Obsolete main behavior | Status / recommendation | Complexity | Dependencies and risks |
| --- | --- | --- | --- | --- | --- |
| 43 | Game artwork ETag/304 and finite caching for unversioned requests. | Assets/variants are served through current host routes and fallbacks; source adds explicit revalidation semantics/tests. | PARTIALLY PRESENT / ADAPT server revisions/revalidation. | Medium | Normalize variants, default art and missing files; cache ownership and upload invalidation. |
| 44 | Client artwork timestamp versions persist across visits; versioned URLs are immutable for a year. | Current UI lacks that source client revision scheme. | REGRESSION RISK / use server-backed content revisions instead. | Medium | 43; a client timestamp is not a content identity and can remain stale across users/devices after the mutable file changes. |
| 45 | Empty movie/TV/anime trash per kind, with permanent-delete confirmation. | Individual soft delete, restore and purge exist; batch empty operation is missing. | MISSING / extend current trash boundary. | Medium | Owned deleted rows only, transaction/response counts and explicit confirmation. Active titles and another account's rows must survive. |
| 46 | Personal media trash retention of never/7/14/30/90 days; periodic purge with never as default. | Existing generic file trash sweep does not implement this personal media-title retention. | MISSING / ARCHITECTURAL PORT. | Medium | 45, existing scheduler/sweep and JSON preferences. Opt-in permanent deletion, clear explanation, bounded work and child cleanup. |
| 47 | Scheduled new-season detection for completed anime franchises, off by default, bounded batches and franchise deduplication. | Related-chain notification helper and airing checks exist; no equivalent dedicated bounded sequel schedule. | PARTIALLY PRESENT / ARCHITECTURAL PORT. | High | Existing JobSpec registry, provider gateway/cache, persisted run cursor, franchise identities, preferences and notification dispatcher. TV season checks are not equivalent. |
| 48 | Notify about new franchise movies/OVA/specials separately, default off. | Sequel/season/movie event support exists; no equivalent dedicated anime-extras preference/event categorization. | PARTIALLY PRESENT / ADAPT events and preferences. | Medium | 47; preserve destination/trust/privacy policy, routes, deduplication and user ownership. Do not recreate source notification infrastructure. |
| 49 | Record failed airing-job attempts so a persistent failure is not retried every minute. | Scheduler records successful runs; the airing failure path logs without equivalent failure/run persistence. | PARTIALLY PRESENT / extend current scheduler failure reporting. | Medium | Retain personal connection/plugin task schedules and continuation semantics introduced on main. No second scheduler/global running flag. |
| 50 | Select due AniList accounts before applying batch limit; record failed import attempt time. | Query limits enabled accounts before due checks and only records successful runs. | MISSING / fair bounded due-work selection. | Medium | Existing personal preferences, scheduler and retries; avoid starvation, preserve manual import and account ownership. |
| 51 | Activity heatmap month labels and richer hover/day interaction. | Current heatmap is static with count tooltips. | PARTIALLY PRESENT / shared accessible chart improvement. | Medium | 52 for detail popup; focus/keyboard equivalents, responsive overflow and local-day/timezone consistency. |
| 52 | Owned activity-by-day endpoint and dialog linking watched episodes/achievements to details. | Existing activity/statistics aggregates do not provide this source day-detail API/dialog. | MISSING / extend current stats/history services. | Medium | Validate date/timezone, scope every join to account, exclude deleted entities, respect integration read scopes and bounded result sizes. |
| 53 | Game statistics and weekly digest ignore trashed titles. | The game overview and digest in stats.py filter ownership without deleted_at; current media_stats and notification scans already exclude trashed media. | PARTIALLY PRESENT / fix the game aggregate predicates; keep existing media exclusions. | Medium | 45–46, 52; preserve exact arithmetic, Plan-to-watch preference and watched-time history semantics. |

## Authentication, security, import validation and settings

| # | Feature / Rosefall behavior | Obsolete main behavior | Status / recommendation | Complexity | Dependencies and risks |
| --- | --- | --- | --- | --- | --- |
| 54 | Exact username lookup before lowercase email fallback prevents ambiguous login matching. | Login still queries username OR email in one scalar lookup. | MISSING / adapt authentication lookup. | Medium | 55, legacy collision regression; preserve disabled-account, password and session semantics. |
| 55 | Reject new/changed usernames containing `@`, while existing usernames still sign in. | Username create/update paths do not provide the same consistent reservation. | MISSING / central username validation. | Medium | 54; cover setup, admin create, profile rename and OIDC-generated names. Do not rename existing accounts or reject them at login. |
| 56 | Changing password invalidates other browser sessions but preserves the current session. | Password change checks the existing password; other browser sessions are not consistently revoked by that route. | MISSING / use current revocation/audit boundary. | Medium | Keep current actor, network/session audit and recovery flows. Prefer revocation timestamps/events to source row deletion. |
| 57 | API keys cannot create more API keys; minting requires an interactive browser session. | API-key creation authenticates a User but does not enforce the equivalent credential-kind guard. Scoped delegation is a separate contract. | MISSING / enforce at the auth API boundary. | Medium | Existing AuthenticatedActor; test API key and delegated actors, browser success, revoke/list semantics and plugin recovery boundaries. |
| 58 | OIDC rejects an explicit `email_verified=false`; absent verification claim remains compatible. | Current identity resolution does not apply this exact rejection. | MISSING / adapt OIDC policy. | Medium | Preserve issuer/subject binding, provider allow-new-user/matching rules and actionable login errors. Do not accidentally require claim=true for every provider. |
| 59 | OIDC email updates handle another account already owning that email without a database 500. | Current subject-bound resolution can assign a changed email without the same explicit collision handling. | PARTIALLY PRESENT / protected collision handling. | Medium | 58; never merge accounts by unverified email; transactional uniqueness and clear failure. |
| 60 | Setup configuration requires an admin after bootstrap. | Public GET /api/setup/configuration remains available after bootstrap. | MISSING / protect the post-bootstrap route. | Medium | Keep initial setup and generated frontend schema usable before an account exists; preserve environment locks and recovery UI. |
| 61 | Mark DATABASE_URL secret so registry schema cannot disclose embedded credentials. | Field is hidden but not marked secret; hidden is not redaction. | MISSING / registry redaction + regression. | Low | 60, EnvConfigHandler redaction; keep connection resolution and startup diagnostics working without exposing secrets. |
| 62 | Public-image URL checks reject unsafe hosts/ports/credentials/redirect targets. | Poster helper has partial public-host/no-redirect checks; game URL download still follows ordinary requests without equivalent public-target validation. New public-app transport already pins validated HTTPS addresses. | PARTIALLY PRESENT / ARCHITECTURAL PORT reusing current safe transport principles. | High | URL/DNS pinning, bounded streaming, redirect validation, MIME/raster validation, async boundary and cache paths. Source alone does not solve DNS rebinding. |
| 63 | Restrict uploaded media types, safe storage filenames and correct extension classification. | Existing media classifier trusts advertised MIME first and lacks the source's complete permitted-extension/filename treatment. | PARTIALLY PRESENT / strengthen existing upload boundary. | Medium | Preserve original display/download names, validate actual bytes where needed, bounded files, plugin document APIs and existing supported formats. |
| 64 | Add nosniff/sandbox response headers to stored media, including range responses. | Range/file response helper does not consistently supply equivalent headers. | COMPATIBLE PORT / security header parity. | Medium | 63; preserve 200/206/416, preview/playback, Content-Disposition and permitted embedded media. |
| 65 | Imported ratings are finite, clamped to 0–10 and normalized to two decimals across supported list sources. | List/Yamtrack imports have separate conversion paths without the shared complete normalization. | PARTIALLY PRESENT / reuse one rating-only normalizer. | Medium | IMDb/Letterboxd/Yamtrack scale conversion, null/blank behavior, Decimal precision. Do not clamp unrelated numeric fields. |
| 66 | Restore rejects NaN/Infinity instead of writing invalid Numeric values. | Existing restore handles types, but source adds a stronger general finite check. | PARTIALLY PRESENT / numeric restore validation. | Medium | 65; retain legitimate zero/null and other precision; errors identify skipped/invalid entries without weakening atomic restore guarantees. |
| 67 | PATCH null means keep a required field, while nullable fields can be cleared. | Game helper already drops nulls for required fields; media locking helper lacks the same complete required-column handling. | PARTIALLY PRESENT / extend validation consistently. | Medium | 30; preserve title actor protection, missing-vs-null semantics and explicit optional clears. Decide ignoring versus 422 at the API boundary deliberately. |
| 68 | Settings search indexes section/tab keywords, supports keyboard selection and hides admin targets from normal accounts. | Current grouped settings with core/plugin sections lack the equivalent search index. | MISSING / ARCHITECTURAL PORT of permission-aware settings search. | Medium | Index current groups/aliases and active plugin contributions; never expose secret values or hidden admin pages. Shared UI for both presentations. |
| 69 | Opening section links selects the correct containing tab. | Current settings resolver already handles legacy section aliases and initialTab in route changes. | ALREADY PRESENT / LEAVE ALONE. | Low | 68 should use the current resolver, not the source's old settings hierarchy. |
| 70 | Dependent calendar/notification toggles are disabled with a reason when the parent choice prevents their effect. | Current richer notification routes/preferences retain availability logic, but not all source explanatory dependencies. | PARTIALLY PRESENT / targeted reason states. | Low | 47–48 where relevant; do not overwrite hidden saved preferences, provider grants or destination trust decisions. |
| 71 | Broad sentence-case, “Could not…”, concise saved/empty/destructive-action wording. | Host already centralizes many request errors and has newer workflow-specific explanations. | COMPATIBLE PORT / selective copy only, optional. | Medium | Keep diagnostic meaning, permission explanations and accessibility. Avoid hundreds of mechanical unrelated edits and raw backend/status leakage. |
| 72 | Reformatted Login/App/Sidebar/startup/password controls and unchanged SSO-first/local fallback flow. | Equivalent core flow already exists; #469 largely reformats it rather than introducing new behavior. | ALREADY PRESENT / LEAVE ALONE. | Low | Preserve current branding, installed themes, offline/startup handling and plugin authentication. |

## Suggested review groups and implementation order

- Security and validation: **54–67**. Implement focused host regressions first;
  these can be independent PRs and should not wait on the form redesign.
- Completion: **1–3** together; keep **4**. Use current sync and manual evidence
  ownership. Explicitly reject status mutation from GET unless separately chosen.
- Form/lookup capability: **14–21, 26–30, 37**. Share the progressive lookup/preview
  foundation, then improve game/media sections. Keep **16** as the host boundary.
- Time-to-beat: **8–9, 24–25**. Agree the additive public fields, then host storage
  and builtin IGDB, then independent HLTB SDK/plugin update and signed-package tests.
- Library correctness: **11–13, 31–41**. Server ordering precedes UI sorting,
  pagination/cache refinements and computed progress decisions.
- Cleanup and notifications: **45–50, 53**. Extend the current sweep/jobs/events;
  retain dispatcher destinations, trust, delivery policy and personal schedules.
- Activity/settings: **51–52, 68, 70–71**. Reuse current services and navigation;
  no parallel search client, settings store or notification system.
- Artwork caching: **43–44**, coordinated with **62** and editor asset workflows.
  Prefer server-backed revisions; reject the direct timestamp/immutable shortcut.

These groups explain dependencies, not a proposed blanket approval. You may choose
individual numbers; dependent selections will be reconciled before implementation.

## Source and host evidence

The [complete changed-file ledger](rosefall-469-files.md) links all 188 source files
(including additions and deletions) at the audited revision and records all five
commits. The ledger is a coverage index; the numbered rows above are behavior
choices, so shared code/tests can contribute to more than one row.

Source: [Rosefall #469](https://github.com/Rosefall-a/unnamed_tracking_app/pull/469),
five commits from `ce1fdab1b` through `2785649b0`. The changed-file inventory contains
188 files, including one time-to-beat migration, new lookup/form/quick-edit/search
helpers and regression tests. Deleted MediaMetadataSearch/shared media form CSS
are replaced by the new source form/lookup architecture. The PR does not change
Docker/workflow infrastructure or wiki/assets.

Host trace anchors: `api/routes/library_sync.py`, `steam_import_steps.py`,
`api/routes/games.py` and `utils/games.py`; `features/imports/library_games.py`;
`features/metadata/{core,providers,handler,configuration,identity,locked_fields}.py`
and `plugin_api/metadata_contracts.py`; `api/routes/media_common.py`;
`features/jobs.py`, `connection_jobs.py`, notifications/dispatcher and trash sweep;
`auth.py`, `auth_oidc.py`, setup/config registry; `helpers/remote_images.py`,
`api/routes/game_assets.py`, media/range helpers; current game/media editors,
library/filter/cache composables, Settings aliases and stats heatmap.

The active optional HLTB source is `official/metadata-hltb/plugin.py` in
`obsoletelabs/unnamed_tracking_app_plugins`, not the similarly named legacy host
file. Optional provider changes must be separate commits/PRs against that project.
Core Steam, IGDB, SteamGridDB, TVmaze, AniList, AniZip and OMDb remain host features.

Source regression tests substantiate the intended behavior (account security,
OIDC claims, setup redaction, remote image safety, ratings, numeric/null updates,
folder rename, game-form save, artwork revalidation, ranking, library sorting,
media trash, failed jobs, new-season notices, daily activity and time-to-beat).
They are reference requirements; they must be adapted to the current host/public
plugin boundaries, not copied wholesale as fixtures or treated as proof that a
direct port is safe.

## Next step after selections

For each selected capability, write the concrete host/plugin file plan, API/model
and migration changes, backward compatibility, tests and documentation. Implement
only selected behavior in reviewable PRs into current main, with UI screenshots
updated as the shared components change. Preserve Playnite, manual/provider
achievement evidence, connected-library consent, plugin runtime/permissions,
offline caches, protected titles, notification delivery policy and user data.
