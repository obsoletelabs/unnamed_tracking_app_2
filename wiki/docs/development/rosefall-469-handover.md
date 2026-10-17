# Rosefall #469 handover

## Current checkpoint — 15 October 2026

The [ordered tracker #141](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/141)
contains every remaining approved Rosefall workstream and related follow-up. Work
one issue at a time, most complex first, respecting dependencies. Separate identity
and notification overhaul projects are outside this queue.

Packages through **#137 are merged into Obsolete Labs main**. The combined library
PR passed all seven full hosted workflows on `36dc10cd0146e00e268f15e201492dd0fde0cfce`
and was squash-merged as `3b597a02403b05a46dfbe1e8e1aff7ae8c58c77f`. It adds game
exclusions, global media sorting/counts, loaded-extent refresh and navigation fixes.
Its local suites passed 2,708 backend tests (four skipped) and 601 frontend tests.

**Active issue: #142 only**, published as
[PR #155](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/155) from
`feat/staged-metadata-lookup`, based on current main after #137. Coherent backend
commit `20c8cb3f6` and frontend commit `a86580db` are pushed; the final documentation
and screenshots accompany them in this PR.

- One checked comparison model/panel serves game/movie/TV/anime editors and the
  actual Add media quick-add review. Apply stages local values; Save persists;
  Cancel discards. Save waits for a pending lookup to be applied or cancelled.
- Empty editable values default checked; populated values, zeroes, manual edits,
  unchecked choices and locks are preserved. Changed titles/selections/artwork
  revisions cannot apply stale responses.
- Provider identity is explicit on edits. TV groups identity and seasons; Save
  adds only higher-numbered seasons and preserves current season tracking.
- Artwork provenance stays in the host session. Owned selected previews recheck
  provider media authorization and use pinned public image fetching/validation.
  Steam tries tall cover then header. No migration, SDK or provider package change.
- Modern/Classic share logic and semantic tokens. The Classic UI guide documents
  how to extend the shared adapter instead of copying old components/search code.

Validation: full backend **2,715 passed, four skipped**, mypy **357 files**, full
configured Pylint **10/10**, Ruff lint/format **577 files**. Full frontend **616
passed**, ESLint, Prettier, vue-tsc and production build passed; all **491 source
files** meet the size limit. The final small result-list visibility adjustment
also passed focused form/search/add tests and types. Live quick-add review exposed
unused provider fields; correction `144d7322` limits its checklist to persisted
fields, retains the selected title/identity, and passed 17 focused tests, types,
lint/format and the size check. The staged-season regression
failed before its fix. Live Steam Portal and TVmaze The Office checks verified
selection, metadata, owned artwork previews, checked Apply and cancelling the form
without database writes. Modern dark and Classic light screenshots are included.

The preview's TVmaze account opt-out was enabled for the live check. Only the
synthetic local account was changed. A phone capture could not be verified because
the integrated browser ignored its viewport override and still reported 1280px;
the invalid capture was removed. Do not claim fresh phone visual verification.

Remaining steps: mark #155 ready and require all
full hosted workflows on its final head before the authorized squash merge. Then
close #142 through the PR, update #141 and start only #143 next. Do not mark #142
complete before final PR validation/merge.

HLTB companion PR #47 is merged and official HLTB 0.7.0 was signed/published
normally. Package/signature validators and execution of its bundled SDK over
simulated public gateway replies passed for all four estimate fields. Earlier live
provider token/search checks passed; this does not claim installed-host live HLTB.

Task-owned checks use `uta-rosefall-oidc` and local Vite port 5179. Scratch logs,
Compose configuration and preview data backups are in this task's visualization
directory. Hosted application data and unrelated Docker projects are untouched.
The earlier approval-service usage limit is resolved.

## Historical checkpoint — 14 October 2026

This is the historical pause checkpoint. Work resumed on 15 October 2026; consult
the [current implementation progress](rosefall-469-implementation.md#current-progress)
for subsequent packages and PRs.

Work is being wrapped up at the owner's request. Resume only when requested.
This page separates completed adaptations from the approved backlog; the
[implementation plan](rosefall-469-implementation.md),
[72-row review matrix](rosefall-469-reconciliation.md) and
[188-file source ledger](rosefall-469-files.md) remain the detailed references.

## Completed delivery

| PR | Delivered behavior |
| --- | --- |
| [122](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/122) | Separate frontend checks; scoped non-main commits, full ready PR/main checks, tests always run. |
| [123](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/123) | Built-in Classic/Modern presentations, native Classic palette, setup/environment default and complete typing-only Automatic Search policy. |
| [124](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/124) | Audited source, reconciliation matrix and approved implementation plan. |
| [126](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/126) | Rosefall items 54–57 and 60–61: username-first/email fallback, safe new/changed usernames with legacy compatibility, password/session handling, browser-only key creation, administrator configuration access and secret redaction. |
| [127](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/127) | Items 58–59: off-by-default verified-email requirement, explicit true claim when enabled, callback-alias enforcement, safe account collision/race handling and additive migration. |
| [128](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/128) | Item 62: shared public DNS validation and connection pinning, TLS identity, checked redirects, bounded byte/pixel validation and owned game artwork URL fetching outside the async event loop. |

PRs 122–127 listed above are merged into Obsolete Labs main. Source Rosefall
commits have not been merged or cherry-picked. C1 is published as PR #128 into main. Its live PR status is authoritative for
hosted checks and merging; it must pass every required check before a squash merge.

## Classic and migration experience

Classic uses Rosefall's familiar near-black/orange palette, shapes, typography and
navigation treatment inside the current application's shared components and state.
It retains Light/Dark/System, with an adapted light palette, and permits native
palette/custom-color overrides plus existing accessibility controls. Modern keeps
the current Archive desktop/Pocket navigation presentation. A native Classic
palette also lets people keep Modern's layout while retaining familiar colors.

`DEFAULT_UI_STYLE=modern|classic` and Setup choose the deployment default for
unconfigured accounts/browsers; explicit saved choices remain authoritative.
The [Classic UI porting guide](classic-ui.md) explains how to add shared behavior
and style it for both presentations. New settings already have Modern/Classic
screenshots committed alongside their PRs. The staged lookup (#142) is merged and
the shared game editor with eight direct tabs is #143; media editor sections (#144)
and quick editors (#149) remain pending. Classic does not imply all #469 features exist.

## Accepted constraints for resumption

- Work from current `obsoletelabs/unnamed_tracking_app_2` main. Rosefall is a separate
  source reference. Use focused PRs into main, or explicitly documented stacks.
  Squash merging is authorized only after completion and all required checks pass.
- Automatic Search controls typing-triggered external metadata calls. The account
  master and per-provider settings apply; administrators may restrict all providers.
  Personal credentials can bypass a restriction on server-owned credentials.
  Local filtering, explicit Search/Enter, selection enrichment and scheduled
  refreshes remain available. Whole-provider opt-outs are a separate unfinished item.
- New/renamed usernames permit ASCII letters, digits, `_`, `-` and `.`, no spaces.
  Existing names remain valid for login/unchanged values. Bound SQL parameters,
  not SQL-keyword blacklists, enforce database safety. Display names are tracked in
  [issue 125](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/125).
- Verified email is optional per OIDC provider and environment bootstrap. Enabled
  requires the explicit boolean true claim, including legacy callback aliases.
- Core Steam, IGDB, SteamGridDB, TVmaze, AniList, AniZip and OMDb are host features.
  Optional plugins/SDK consumers belong in the separate
  `obsoletelabs/unnamed_tracking_app_plugins` repository. Preserve Plugin API v1,
  package integrity, permissions and the single migration history.
- Keep shared UI behavior in existing services/components. Classic styling belongs
  in centralized tokens/selectors. Keep UI screenshots and PR descriptions current.

## Remaining approved work

| Package | Remaining implementation |
| --- | --- |
| C2 / 63–64 | Actual upload content/extension validation, safe stored names and preserved original names, full/range response nosniff/sandbox headers. |
| D / 30, 65–67 | Finite normalized ratings/imports, numeric input and nullable PATCH semantics. |
| E / 23 | Whole-provider account opt-outs, including server credentials; preserve saved order and default Steam before GOG. |
| F / 1–3, 5, 10, 12–13 | Event-driven completion, safe folder rename, reconciled cards/counts and priority/resume consistency. |
| G / 8–9, 24–25 | Additive time-to-beat fields and enrichment; separate HLTB SDK/provider updates and actual package validation. |
| H / 11 | Exclude status/tag/platform filters and compatible saved presets. |
| I / 31–36, 39–41 | Stable sorted pagination/counts, refresh/stale-response handling, collection decoding and list reorder fixes. |
| J / 15, 17, 28–29 | Shared staged metadata comparison/lookup and identity-bound art/TV selection without writes until save. |
| K / 6–7, 14, 18–21, 26–27, 37–38 | Shared editor/eight tabs, lazy art, richer and quick editing, recoverable partial saves. |
| L / 43–44 | Server-content artwork revisions and ETag/304 behavior. |
| M / 45–46, 53 | Owned media trash clearing/opt-in retention and corrected statistics predicates. |
| N / 47–50, 70 | Fair bounded due jobs, failure cadence, anime follow-ups/notification reasons. |
| O / 51–52, 68, 71 | Owned activity day details/month labels, settings search and clearer affected copy. |

Retain the current implementations for 4, 16, 22, 42, 69 and 72. Item 3 is an
event-driven adaptation: no status mutation on GET. Item 44 must use actual server
content identity, never a client timestamp presented as immutable.

## Next concrete steps

1. Check the C1 PR's final status below and pull current main before beginning C2.
2. Trace actual media upload/download callers, `helpers/media.py`,
   `helpers/range_response.py` and MediaItem persistence. Original filenames currently
   have no dedicated column. Decide whether a reviewed additive migration is required
   before implementing that part; preserve user-edited titles and existing playback.
3. Reproduce spoofed upload/range behavior, add public-route regressions and preserve
   legitimate 200/206/416 responses. Inspect plugin document consumers independently.
4. Continue D then E under the approved package order. Check actual optional plugin
   packages when their contract/consumer behavior is affected; do not copy them here.

C1 extracted the existing delegated/public-app pinned transport into
`helpers/public_http.py`. The public-app HTTPS-only, no-redirect and 2 MiB raster
rules remain unchanged; general permission-granted plugin HTTP policy is separate.
There is no plugin SDK contract change or plugin implementation update for C1.

## Final validation and checkout

C1 is committed and pushed on [PR #128](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/128).
Local validation:

- Complete backend suite: **2,477 passed, four skipped**.
- Focused asset/cache/public-app/catalogue suite: **103 passed**.
- Complete frontend suite: **531 tests / 93 suites passed**.
- Full backend mypy: **352 source files**, no errors.
- Configured Ruff lint and formatting: **558 files**, passed.
- Complete frontend ESLint, Prettier and vue-tsc: passed.
- Backend file-size check: **352 files**, passed.
- Strict MkDocs build: passed.

Configured full Pylint and all hosted checks must be green before merging. Check
the live PR for their final results; do not treat a draft/skipped or older-head run
as evidence for the current head. No checks or assertions were removed to pass.
The new unsafe-URL regressions reproduced 11 failures before enforcement; the
finished suite covers checked public redirects, private/mixed/rebound DNS, TLS
identity, byte/pixel limits, corrupt content and owned route behavior.

Active C1 checkout:
`C:\Users\toast\.codex\worktrees\scoped-frontend-ci\unnamed_tracking_app-1`,
branch `fix/public-asset-fetch`. Previous account-security and OIDC work is committed,
pushed and merged. OIDC migration `0e3353138981` follows `83bb783fd35e` and was tested
on populated upgrade, adoption/replay and downgrade paths.

Disposable local checks use the task-owned `uta-rosefall-security`,
`uta-rosefall-oidc` and `uta-classic-tests` Compose projects. Compose files, check logs
and preview configs are under the task's visualization scratch directory. They use
synthetic accounts and isolated databases, not hosted application data. Existing
hosted Rosefall/Obsolete instances and unrelated running projects are untouched.
Local preview services are separate from the checks and can be stopped or restarted
using those task-owned Compose files/configs. They are not hosted deployments.
No agent or automation has been scheduled to continue work after wrapping up.
