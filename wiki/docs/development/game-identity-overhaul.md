# Game identity overhaul: coordination board

Coordination page for the epic
[#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91). It links the child
issues, pull requests, invariants, contracts, merge order and open risks. The detail lives in the
per-phase pages listed in section 9; this page does not repeat it.

Status was read from GitHub on 2026-10-17 (`main` at `f8452cd6`, after #180 and the Alembic
merge revision #237). It is a snapshot: re-check before merging.

## 1. Dependency graph

```text
#92 audit -> #93 model -> #94 ingestion -+-> #95 merges/decisions -> #96 counters -+-> #97 manual tracking ---+
                                         |                                         +-> #98 provenance/names --+
                                         |                                         +-> #99 matching/queue ----+-> #101 UI -> #102 migration -> #103 acceptance
                                         |                                         +-> #100 Playnite ---------+
```

Hard rules from the epic: the audit precedes schema design, the ingestion and identity contracts
precede integration changes, and migration is validated before any legacy path is removed (no
legacy path has been removed). #97, #98, #99 and #100 are independent of each other and all build
on #96. #101 needs all four. Each is stacked on the pull request before it.

## 2. Status board

| Issue | Scope | Pull request | State | Based on | Page |
| --- | --- | --- | --- | --- | --- |
| [#92](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/92) | Audit | [#164](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/164) | **merged** (issue closed) | `main` | [Audit](game-library-identity-audit.md) |
| [#93](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/93) | Canonical data model | [#180](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/180) | **merged** | `main` | [Model](game-identity-model.md) |
| [#94](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/94) | Idempotent ingestion | [#196](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/196) | open, ready | `main` | [Ingestion](game-ingestion.md) |
| [#95](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/95) | Reversible merges, decisions | [#202](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/202) | open, ready | #196 | [Decisions](game-identity-decisions.md) |
| [#96](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/96) | Counter groups, adjustments | [#205](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/205) | open, ready | #202 | [Counters](game-identity-counters.md) |
| [#97](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/97) | Manual achievements, completion | [#209](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/209) | open, ready | #205 | [Manual](game-identity-manual.md) |
| [#98](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/98) | Name provenance, placeholders | [#210](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/210) | open, ready | #205 | [Names](game-identity-names.md) |
| [#99](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/99) | Matching, review queue | [#213](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/213) | open, ready | #205 | [Matching](game-identity-matching.md) |
| [#100](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/100) | Playnite host side | [#212](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/212) | open, ready | #205 | [Playnite](playnite-observations.md) |
| #100 | Playnite extension (other repo) | [unnamed_tracking_playnite#2](https://github.com/obsoletelabs/unnamed_tracking_playnite/pull/2) | open, **draft** (C# not built here) | its `main` | - |
| [#101](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/101) | Umbrella / versions / identity UI | [#214](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/214) | open, ready | #213 (carries #97, #98, #100) | [UI](game-identity-ui.md) |
| [#102](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/102) | Safe existing-library backfill | [#216](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/216) | open, ready | #214 | [Migration](game-identity-migration.md) |
| [#103](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/103) | Acceptance, performance, operations | [#222](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/222) | open, **draft** | #216 | [Acceptance](game-identity-acceptance.md) |
| #91 | This board | the pull request that adds this page | open, stacked on #222 | #222 | this page |

#92 and #93 are on `main`. The next unblocked work is to review and merge #196; every other
pull request is stacked on it. #103's pull request (#222) stays a draft until the verification
listed in section 8 is done.

Existing issues the epic told us to coordinate, checked on the same date:

| Issue | State | Handling |
| --- | --- | --- |
| [#56](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/56) duplicates do not dedupe | closed | Owned-copy duplicate groups are kept and composed with umbrellas ([UI](game-identity-ui.md)); suggestions go through the durable review queue (#99). |
| [#72](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/72) Steam linking, incomplete inventories | closed | Edge cases are encoded in ingestion: a Steam omission never marks anything unavailable; a partial inventory changes nothing ([Ingestion](game-ingestion.md), acceptance scenario 11). |
| [#67](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/67) background library syncs | **open** | Not part of this epic. Its contract: it must call the ingestion service (idempotent) and never bypass decisions or locks. |

## 3. Invariants (non-negotiable)

1. A source reports observations. It does not own identity and cannot overrule a user decision.
2. Game, version/edition, source observation, source identity, relationship and counter are
   different things. Merge never means deleting rows.
3. Source history survives merge, split, disconnect and rename. Unmerge restores both sides.
4. User decisions beat automatic matching and provider metadata. "Keep separate" suppresses that
   pair for good; a genuinely new third record is still reviewed against either record.
5. Manual achievements, completion, playtime adjustments and locked fields are never overwritten
   by an import.
6. Duplicate observations of one counter are never summed; independent counters may be.
7. Placeholder names ("Steam App 12345") never lock a title.
8. Playnite stays usable in every phase: the original routes keep paths, payloads and codes.
9. The migration is additive and resumable; no destructive reset or re-import.

## 4. Decision rules for reviewers and later work

| Question | Rule |
| --- | --- |
| Source says X, the user chose Y | Y wins. Record X as an observation; do not apply it. |
| Two records look alike | Merge automatically only on exact corroborating evidence (shared stable IDs); everything else is queued for review. Titles alone never merge. |
| User rejects a suggestion | Final for that pair. Re-propose only when new evidence concerns a different record. |
| Merged Playnite and manual counters | One counter, one reading (not 200 h from 2x100 h). Steam and Xbox are independent: summed. |
| Anything ambiguous or unusual that the user asked for | Do it and explain consequences; do not refuse or fight the choice. |
| Information missing in a legacy row | Leave it unknown. Never guess a plugin ID or account scope. |
| Need to remove legacy behaviour | Only after the backfill has been validated on populated data (#102, #103). |

## 5. Cross-repo contracts and versions

| Contract | Version | Where | Notes |
| --- | --- | --- | --- |
| Playnite evidence API | `contract_version` 1 (`CURRENT_VERSION = 1`, `SUPPORTED_VERSIONS = (1,)`) | `features/game_ingestion/playnite.py`, `/api/playnite/contract`, `/observations`, `/inventory` | Unsupported version: HTTP 400 `unsupported_contract_version`, nothing stored. See [Playnite](playnite-observations.md). |
| Original Playnite routes | unchanged | `/api/game/list`, `/create`, `/update/{id}`, assets, archives | Must stay; an extension that never negotiates keeps working. |
| Playnite extension | probes `/api/playnite/contract`; falls back to the original routes on 404 or an unknown version | `obsoletelabs/unnamed_tracking_playnite` #2 | Host can ship first; the extension is optional-upgrade. |
| Plugin API | `PLUGIN_API_CONTRACT_VERSION` | `obsoletelabs/unnamed_tracking_app_plugins` | Not changed by the epic. Store plugins feed observations through the ingestion service with their plugin ID and account scope. |

Release order: the host pull requests first (they are backward compatible), then the extension.
No plugin-repo change is required by any child.

## 6. Merge order and Alembic chain

Merge strictly in this order, retargeting each pull request to `main` after its base merges:
**#196, #202, #205, then #209, #210, #213 and #212 (any order among them, but #213 before
#214), #214, #216, #222, then this page**; the extension #2 after the host pull requests.

Migration chain (files under `src/backend/src/database/migrations/versions/`):

```text
<main head> -> a3c5e7b9d102 (#102 backfill progress) -> c4e8a1f6b3d7 (#103 library list index)
```

`716a1e2c451d` (#93 canonical model) is already on `main`, and the merge revision
`50b007897f73` (#237) joined it with the display name revision `a7d3c91e5b20`, so `main` has one
head today. #94 to #101 add no migration. Only `a3c5e7b9d102` (#102) and `c4e8a1f6b3d7` (#103) are
still to land. The stack branches were written before #180 merged, so their `a3c5e7b9d102`
declares `716a1e2c451d` as its `down_revision`; merging it as is would create a second head next
to `50b007897f73` and fail the CI migration-graph job. Before merging #216, merge `main` into the
stack and set the `down_revision` of `a3c5e7b9d102` to the then-current `main` head (and keep that
on every later branch of the stack). Other open pull requests that add a migration will do the
same, so re-check `alembic heads` after each merge. This page changes no migration.

## 7. Verification per child

Run from `src/backend` with the environment of `/opt/utapp/env.sh` (a fresh database, then
`alembic upgrade head`); use `pytest -q -p no:cacheprovider <file>`.

| Child | Focused tests |
| --- | --- |
| #93 | `tests/test_game_identity_model.py` |
| #94 | `tests/test_game_ingestion.py`, `tests/test_game_ingestion_compat.py` |
| #95 | `tests/test_game_identity_decisions.py` |
| #96 | `tests/test_game_counters.py` |
| #97 | `tests/test_game_manual.py` |
| #98 | `tests/test_game_names.py` |
| #99 | `tests/test_game_identity_matching.py` |
| #100 | `tests/test_playnite_contract.py`, `tests/test_playnite_observations.py`; extension: `tests/Companion.Tests` (needs `dotnet`) |
| #101 | `tests/test_game_identity_overview.py`; front end `npm run test` |
| #102 | `tests/test_game_identity_backfill*.py`, `tests/test_game_identity_migration*.py` |
| #103 | `tests/test_game_identity_acceptance.py`, `tests/test_game_identity_scale.py` |

Whole-stack gate (on the #222 branch, as recorded in [Acceptance](game-identity-acceptance.md)):
`ruff format --check .`, `ruff check .`, `mypy --config-file pyproject.toml src`,
`pylint --rcfile=pyproject.toml src`, `alembic upgrade head` and `alembic check`, the full
`pytest`, and in `src/frontend` `npm run lint`, `format`, `typecheck`, `test`, `build`. Docs:
`mkdocs build --strict --config-file wiki/mkdocs.yml`. The acceptance page reports the numbers.

## 8. Open risks and limitations

- Hosted CI has not confirmed the stack. The Docker build, production smoke test, plugin
  integration workflow and the extension's .NET build and tests were not run in the sandbox.
  This is why #103 and extension #2 are drafts.
- Two plugin-upload tests fail in the sandbox only (no DNS); they also fail on clean `main` here.
- The migration-head move in section 6 must be fixed at merge time.
- Several paths are linear in CPU (library layout, duplicates list); the library list is paged at
  200 rows. The first matching run on a large unmatched library is slow and holds the user's
  identity lock. See [Acceptance](game-identity-acceptance.md), sections 6 and 9.
- Read endpoints (`metrics`, `overview`, `sources`) may create counters lazily.
- Backfill cannot recover plugin IDs or account scopes that legacy rows never stored; those
  games keep updating and gain scoped observations on their next import. Scheduled backups do not
  contain identities and are not a rollback ([Migration](game-identity-migration.md), section 6).
- The stack is long. A late change to #180 or #196 means rebasing every later pull request.

## 9. Definition of done for #91

| Criterion from the epic | Evidence | State |
| --- | --- | --- |
| Each child has tests and acceptance evidence | Pages above; test files in section 7 | Written; only #92 merged |
| No data loss in populated migration tests; merge and unmerge reversible | `test_game_identity_migration*`, scenarios 3 and 11 | Passing locally |
| Repeated imports idempotent | `test_game_ingestion.py`, scenario 1 | Passing locally |
| Playnite works with real payloads and existing connections | `test_playnite_*`, backfill Playnite tests | Host passes; extension not built |
| Manual edits survive imports | scenario 9 | Passing locally |
| All relevant CI passes | - | **Not yet known** |
| Docs explain model, workflows, source contract, migration | Pages in section 2 | Done |

The epic is complete when #180 to #222 and the extension are merged, hosted CI is green on the
merged result, and the three open items in section 8 (CI, extension build, head rebase) are
closed. This page does not close the epic.
