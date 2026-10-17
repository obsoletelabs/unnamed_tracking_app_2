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
  The follow-up for automatic library enrichment and game metadata refresh is
  merged in the combined [PR #135](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/135):
  both use that boundary and effective configured image limits. Six regressions
  reproduced the old downloader bypass; coverage also preserves safe candidate
  fallback. No SDK change or broader provider permissions are needed.
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
- **E / 23:** merged as [PR #131](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/131).
  Account provider opt-outs cover builtin/plugin discovery, current invocation,
  cached responses, direct metadata actions and scheduled metadata work. Saved
  credentials and typing choices remain intact. Library-sync actions keep their
  existing permission path. Steam precedes GOG only in unsaved defaults.
  All full hosted checks passed before the authorized squash merge: 2,586 backend
  tests (four skipped), 569 frontend tests, and full configured lint/type checks.
  Sixteen separate SDK/provider contract tests also passed.
  Shared settings screenshots cover [Modern](../assets/provider-choices/modern-dark.jpg),
  [Classic dark](../assets/provider-choices/classic-dark.jpg),
  [Classic light](../assets/provider-choices/classic-light.jpg) and
  [phone](../assets/provider-choices/classic-dark-phone.jpg).
- **F UI / 10, 12–13:** merged as [PR #133](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/133).
  Card saves reconcile the library, filters/counts, current preview and entity cache;
  older reads cannot undo a successful save. Active priorities use the existing
  priority policy. Finished games keep written notes and a compact keyboard-accessible
  action for empty notes. Full local frontend validation passed 579 tests;
  all seven full hosted workflows passed before the authorized squash merge.
  Screenshots cover [Modern dark](../assets/game-card-consistency/modern-dark-priority.jpg),
  [Classic dark](../assets/game-card-consistency/classic-dark-priority.jpg),
  [Classic light](../assets/game-card-consistency/classic-light-priority.jpg),
  [phone](../assets/game-card-consistency/classic-dark-phone-priority.jpg),
  [finished notes](../assets/game-card-consistency/classic-dark-finished-note.jpg) and
  [retained written notes](../assets/game-card-consistency/classic-light-written-note.jpg).
- **F progression / 1–3:** merged as [PR #134](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/134);
  full local backend tests passed (2,669; four skipped), along with mypy and
  Ruff and full Pylint (10/10). Fifty-one separate Epic plugin consumer tests
  passed without SDK changes.
  One host policy handles owned-library,
  delayed Steam achievement, explicit achievement refresh and public plugin
  import writes. Only fresh provider evidence advances eligible Backlog/Wishlist;
  Wishlist needs owned-library evidence. On 15 October the owner clarified that
  every existing Played choice stays unchanged, including full completion.
  Achievement GET remains read-only; all providers recheck the current locked
  identity/status after a delayed single-game refresh. The plugin schema is unchanged.
  All seven full hosted workflows passed before the authorized squash merge.
- **F folders / 5:** merged in combined [PR #135](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/135), which
  implements folder moves/recovery, coordinated file operations, artwork locking
  and the related enrichment downloader follow-up as logical commits in one PR.
  The artwork-only commit passed full local backend tests as unprivileged
  UID 1000 (2,674; four skipped), full Ruff, mypy and configured Pylint (10/10).
  Ordinary artwork writes retain a shared
  game lock through PNG/preview completion, including cancellation. Imports and
  enrichment use their current locked row, including uncommitted new games.
  Five regressions cover the lock lifetime, repeated cancellation and import
  visibility. Owned folder edits now preserve the full directory and stable
  game-ID URLs; destinations/symlinks are rejected, failed saves reconcile against
  the persisted name, and interrupted moves reserve their names until recovered.
  File/media/note/archive/artwork and public plugin document paths take shared
  locks; previews and folder saves finish before cancellation releases resources.
  The final affected storage/security suite passed 185 tests as unprivileged
  UID 1000, including nine folder-move regressions and concurrent archive upload.
  Full backend validation passed 2,690 tests (four skipped) after correcting an
  upload-limit resolver regression with its existing assertions preserved. All
  seven full hosted workflows passed before the authorized squash merge, including
  full frontend checks and configured Pylint. The plugin API/SDK and database schema
  are unchanged by that package.
- **G / 8–9, 24–25:** host PR #136 and companion plugin PR #47 merged. The additive API/data foundation supplies three
  nullable completion-time categories, protected editing/history, export/import,
  metadata refresh and public game details. The generated/reviewed migration is
  linear after `fed12ab64423` and preserves Main story and personal playtime.
  Native IGDB now fills missing estimate categories after other useful sources.
  The separate HLTB/SDK update initializes search and renews a rejected token once
  through the public gateway. Shared Modern/Classic editors, overview/details and
  statistics expose the categories without changing personal playtime. Host
  validation passed 2,705 backend and 582 frontend tests, full configured quality
  checks and all seven hosted workflows. Companion Linux checks passed 648 tests
  and paired Plugin Manager integration. Modern/Classic desktop/phone screenshots
  are recorded in the game guide. Official HLTB 0.7.0 was signed/published normally;
  signature/package validators and execution of its bundled SDK with simulated
  public gateway replies passed for all four estimate fields. Direct live provider
  token/search verification also passed; this is not a claim of installed-host live
  provider execution.
- **H and I:** merged in combined [PR #137](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/137). Exclusions and saved
  query/preset compatibility, percent-containing collection names, safe filtered
  reorder and episode-page preservation pass 25 focused regressions. The pushed
  global media sorting/counts and shared loaded-extent refresh implementation passes
  21 focused frontend paging/header/recovery tests. Full backend validation passed
  2,708 tests (four skipped), mypy passed 356 files and Ruff/format passed 574 files.
  Affected configured Pylint passed at 10/10. Full frontend checks passed 601 tests
  across 102 suites, ESLint, Prettier, vue-tsc and production build. Modern dark
  and Classic light browser checks verified sorting, keyboard reversal and
  independent tab counts; screenshots are in the media library guide. The
  temporary approval-service limit is resolved. All seven full hosted workflows
  passed before the authorized squash merge into main.
- **J / 15, 17, 28–29:** issue #142, merged as [PR #155](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/155).
  Shared checked add/edit comparison, current protection/manual-edit handling,
  identity-bound verified artwork and staged TV seasons are committed/pushed.
  Game Repull no longer persists before Save. The actual Add media flow also
  supports review while retaining tracking inputs. Full local backend 2,715 tests
  (four skipped), frontend 616 tests, configured quality/build and size checks
  passed. Live Steam/TVmaze checks and Modern/Classic desktop screenshots passed;
  fresh phone visual verification remains unavailable because the integrated
  browser ignored its viewport override. Full hosted checks/merge remain pending.
- **N / 47–50:** issue #145 on `claude/issue-145-fair-scheduled-anime-franchise-checks`.
  Due AniList accounts are selected before the batch limit and every scheduled attempt
  is recorded; failed airing/plugin-update runs are recorded on their cadence. The
  bounded, resumable `anime_franchise_check` job and the opt-in season and
  movie/OVA/special types are documented in the task and notification guides.
  Row 70 remains with #153.
- **K–O:** approved; not implemented yet, except where noted above. The package
  details below describe intended work, not completed behavior.
- **#140 compact tag filters:** the Games filter panel replaces per-tag include/exclude
  chip walls with one searchable, keyboard-operable picker (counts, include/exclude
  modes, merged-spelling hints) plus deterministic tag normalization in
  `utils/genres.ts`, documented in the games guide. Classic and Modern share it.
- **K game editor / 6–7, 14, 18–19:** issue #143, merged in PR #171. One shared
  `GameFormModal` on the new `EditorShell` frame serves Modern and Classic: hero,
  eight direct tabs, a bounded body and pinned actions, with no add wizard.
  Favorite, manual collections, manual playtime and the resume note travel in
  the game's own create/update request. Artwork is built on first use and kept;
  an explicit local file wins until another explicit choice, same-file
  reselection works and object URLs are released. Artwork failures keep the
  saved game and retry on it without another create. Playtime beyond the stored
  INTEGER range is now a validation error rather than a database failure.
- **K / #144, #149 and L–O:** see the status board below for live PR state; only
  merged rows are complete.
- **K media editors / 26–27:** issue #144, in review in [PR #181](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/181), stacked on the merged #143 editor. Movie, TV and anime editors share `MediaFormShell` on `EditorShell`: Basics, Details, Tracking, Artwork and Links tabs, pinned actions, and staged metadata lookup above every tab. Rewatches, dates, source, subratings, tags, countries, languages, age rating, features, anime format and validated provider IDs use each type's existing create/update request. Seasons, progress, title protection and immutable identity remain unchanged.
- **Remaining K (#149) and L–O:** approved; not implemented yet. The package details below describe intended work, not completed behavior.
- **M / 45–46:** issue [#146](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/146)
  implements per-kind Empty trash (explicit `confirm: true`, confirmed deletion-time
  bound, 200-title batches, per-title savepoints and deleted/failed/remaining counts)
  and opt-in `media_trash_retention_days` (Never default; 7/14/30/90) in the existing
  trash sweep with a bounded budget and an in-transaction recheck. Purging stays in
  `features/trash/media_titles.py`, separate from the #151 game-file sweep paths.
  No migration. Modern/Classic screenshots are in the media library guide.
  Merged as #168; row 53 was delivered separately by merged #166.
  Review, hosted checks and merge remain pending; row 53 stays with #152.
- **L / 43–44:** issue #148. Game artwork, normalized `?w=` variants and generated
  covers carry content-derived `ETag`s with `304` handling; unversioned URLs have a
  finite 60-second cache, `?v=<revision>` URLs are immutable, and saving artwork
  invalidates every cached variant. Semantics are in the
  [architecture guide](architecture.md).
- **O / 51–52 (activity heatmap and day detail):** issue
  [#147](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/147), stacked on the
  game-statistics predicate from #152. A bounded owned day endpoint, month labels, keyboard
  and pointer access and one shared Modern/Classic dialog are implemented; see
  [Statistics activity](../user-guide/statistics.md) and the architecture notes. Settings
  search (68) and selective copy (71) in package O are separate.
- **K, N and the rest of O:** approved; not implemented yet. The package details below describe
  intended work, not completed behavior.

### Issue-by-issue execution order

The owner requested one issue at a time, most complex first, on 15 October 2026.
[Tracker #141](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/141)
contains the ordered queue, dependencies and existing related follow-ups #125,
#132 and #138–140. #142 is merged in #155 and #143 in #171; see the status board below.
#132 and #138–140. #142 is merged in #155; #143 is merged in #171, and #144 is in review in [PR #181](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/181).

All 29 untouched approved matrix rows are assigned exactly once:

| Issue | Remaining work | Matrix rows |
| --- | --- | --- |
| #142 | Shared staged lookup | 15, 17, 28, 29 |
| #143 | Full game editor | 6, 7, 14, 18, 19 |
| #144 | Shared media editor sections/fields | 26, 27 |
| #145 | Fair due jobs and bounded anime franchise checks | 47–50 |
| #146 | Empty media trash and opt-in retention | 45–46 |
| #147 | Owned day activity and accessible heatmap | 51–52 |
| #148 | Server-backed artwork revalidation | 43–44 |
| #149 | Shared quick editors and tracking details | 20, 21, 37, 38 |
| #150 | Permission-aware settings search | 68 |
| #152 | Trashed-game statistics predicates | 53 |
| #153 | Dependent preference explanations | 70 |
| #154 | Selective final copy/migration consistency | 71 |

#151 separately tracks the known legacy game-file trash sweep path/locking
follow-up from #135. Existing issues are reused rather than duplicated. The
separate notification and game-identity overhaul epics remain outside this queue.

### Status board for the #141 queue

Verified against GitHub on 17 October 2026 (about 04:00 UTC). A PR is "merged" only
when GitHub reports it merged; open and draft PRs are not done, whatever their own
checklists say. Local results quoted below are the authors' own PR-body claims; the
two `tests/test_plugin_upload.py` failures noted there are sandbox network failures
on clean main, and hosted CI decides. Re-check the live PR before acting on any row.

**Verified prerequisites (merged):** #137 (squash `3b597a02403b05a46dfbe1e8e1aff7ae8c58c77f`, PR [#137](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/137)) and
#142 (PR [#155](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/155), squash `e82e988dc873c6d9f2d93628a75f621b403c1f5d`). Companion HLTB
estimates PR (plugins repo #47) and host [#136](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/136) are also merged.

| Order | Issue | PR | State | Base / stack | Tests | Screenshots | Docs | Companion | Remains |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | #143 | [#171](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/171) | Merged | main | Frontend suites reported | Modern/Classic | Editor and Classic guides | None | Nothing |
| 3 | #144 | [#181](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/181) | Open | main (now unblocked by #171) | 665 frontend pass; no backend change | Modern/Classic, 390px | Yes | None | Review and hosted checks; #149 stacks on it |
| 4 | #145 | [#175](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/175) | Open | main | Backend 3118 pass (2 sandbox network failures), frontend 726, strict docs | Yes | Task, AniList and notification docs | None | Review and hosted checks; #153 stacks on it |
| 5 | #140 | [#177](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/177) | Merged | main | Reported in PR | Reported in PR | Yes | None | Nothing |
| 6 | #146 | [#168](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/168) | Merged | main | Reported in PR | Modern/Classic | Media library guide | None | Nothing |
| 7 | #132 | [#172](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/172) | Merged | main | Reported in PR | Reported in PR | Yes | None | Nothing |
| 8 | #147 | [#193](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/193) | Open | Base is the merged #152 branch; retarget to main | Backend 2790 pass, frontend 633; build and strict docs not run | Yes | Yes | None | Retarget/rebase on main, rerun build and strict docs, hosted checks |
| 9 | #148 | [#197](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/197) | Open | main | Backend 3104 pass, frontend 727, build; live two-client ETag check | Not applicable (no visible UI) | Yes | None | Review and hosted checks |
| 10 | #125 | [#201](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/201) | Open | main | Backend 3140 pass, frontend 728; migration, single head | Yes | Yes | None | Review and hosted checks; its migration head must follow whatever else merges first |
| 11 | #149 | [#200](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/200) | Open | Stacked on #144 branch | Frontend 676 pass, lint, strict docs; live checks | Modern/Classic, 390px | Yes | None | #144 must merge first, then retarget to main |
| 12 | #150 | [#160](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/160) | Merged | main | Reported in PR | Reported in PR | Yes | None | Nothing |
| 13 | #151 | [#174](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/174) | Open | main | 23 new tests (fail on main, pass here); backend 2771 pass | None | Yes | None | Review and hosted checks; coordinated with merged #146 |
| 14 | #139 | [#182](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/182) | Merged | main | Reported in PR | Reported in PR | Yes | None | Nothing |
| 15 | #152 | [#166](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/166) | Merged | main | Reported in PR | Not applicable | Yes | None | Nothing |
| 16 | #138 | [#191](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/191) | Draft | main | 228 focused backend pass; full backend, build and whole-tree lint not run; one load-related timeout in a frontend test | Yes | Yes | None | Full backend suite, build, lint, then mark ready |
| 17 | #153 | [#198](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/198) | Open | Stacked on #145 branch | 731 frontend pass, lint, build, strict docs; backend untouched | Not in PR body | Yes | None | #145 must merge first, then retarget to main |
| 18 | #154 | None | Not started | Last | None | None | None | None | Everything: selective wording and final migration-consistency review |

Only #143, #140, #146, #132, #150, #139 and #152 are merged. #144, #145, #147, #148, #125,
#149, #151, #153 are open and ready for review; #138 is a draft; #154 has no branch or PR.
Matrix rows are delivered only by merged PRs: 6, 7, 14, 18, 19 (#143), 45-46 (#146),
53 (#152) and 68 (#150). Rows 26-27 (#144), 43-44 (#148), 47-50 (#145), 51-52 (#147),
20-21 and 37-38 (#149), 70 (#153) and 71 (#154) are not yet delivered.

**Recommended merge order and stacking.** Independent PRs to main in any order, each
after its hosted checks: #145, #148, #151, #125, #144, #138 (once ready). Stacks: #144 then
#149; #145 then #153 (retarget each to main after its base merges, merging main forward
rather than force-pushing); #147 retargeted to main now that #152 is merged. #154 is
last, after #144, #145, #149, #150, #153 so its wording review covers the final
workflows. Migrations: #125 and #145 each carry migrations and must keep a single Alembic head;
rebase the later migration on the earlier one's merged head. Unrelated queued PRs
(notification and identity projects, #188) are outside this queue; their contracts must
be respected if they land first.

**Per-issue verification** (from `src/backend` with the environment sourced for backend,
`src/frontend` for frontend; `mkdocs build --strict --config-file wiki/mkdocs.yml` for docs):

| Issue | Commands |
| --- | --- |
| #144, #149, #153 | `npm run lint`, `npm run format`, `npm run typecheck`, `npm run test`, `npm run build`; strict docs build |
| #145 | `pytest -q -p no:cacheprovider`, `ruff check .`, `mypy --config-file pyproject.toml src`, `alembic upgrade head`, plus the frontend set above |
| #147 | `pytest tests/test_activity_days.py tests/test_media_stats_sections.py tests/test_game_stats_trash.py`, full backend, `npm run test`, `npm run build`, strict docs |
| #148 | Backend suite, `npm run test`, `npm run build`; two-client ETag/304 check |
| #125 | Backend suite, `alembic heads` (single), `alembic upgrade head` on a fresh DB, frontend set |
| #151 | `pytest tests/test_trash_sweep_storage.py tests/test_trash_sweep.py`, full backend |
| #138 | `pytest tests/test_username_validation.py tests/test_account_security.py`, full backend, frontend set |
| #154 | Strict docs build, grep of changed copy, `alembic heads`, full suites of the affected workflows |

**Definition of done for #141.** The umbrella closes only when every child above is
merged and #154's final consistency review has landed. That is not yet true, so the
status PR for this board does not close #141.

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
manual completion and all statuses other than eligible Backlog/Wishlist. Existing
Played choices never advance automatically, even with full provider completion.
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
