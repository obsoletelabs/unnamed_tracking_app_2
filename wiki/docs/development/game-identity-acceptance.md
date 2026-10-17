# Game identity: final acceptance, performance and operations

Final gate of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#103](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/103). This page records
what was run, what it showed and what was fixed because of it. Every command and number below
was produced in this change's working copy; nothing is quoted from memory. Backend paths start
`src/backend/`.

## 1. Verdict

| Question | Answer |
| --- | --- |
| Is every row of the #91 scenario matrix an executable end-to-end test? | Yes (section 3). They drive the real HTTP routes as the Playnite extension and the web app do. |
| Were defects found? | Yes, five, all fixed here (section 5): a request that failed when two Playnite creates raced, four scaling problems (library layout, overview, review/matching, backfill) and a missing library-list index. |
| Is any known data loss left? | None found. Every scenario asserts that observations, links, games and history are unchanged after merge, split, disconnect, partial import and repeated migration. |
| Was the Playnite extension (C#) built and tested? | **No.** `dotnet` is not installed in this environment (`which dotnet` prints nothing). The host side of the contract is covered by the backend tests below; the extension's own tests (`tests/Companion.Tests` in `obsoletelabs/unnamed_tracking_playnite`) must be run by the repository's CI (section 8). |
| Is hosted CI green? | Not known from here. Local results are in section 4; the hosted jobs that cannot run in this sandbox are listed there. |

## 2. Children of #91 and their pull requests

The live status board, merge order and migration-head notes are kept in the [coordination board](game-identity-overhaul.md); this table is the record as of this report.

Stacked: each pull request is based on the one before it, so they merge in this order.

| Issue | Pull request | Based on |
| --- | --- | --- |
| [#92](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/92) audit | [#164](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/164) | `main` |
| [#93](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/93) data model | [#180](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/180) | `main` |
| [#94](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/94) ingestion | [#196](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/196) | #180 |
| [#95](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/95) merges and decisions | [#202](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/202) | #196 |
| [#96](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/96) counters | [#205](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/205) | #202 |
| [#97](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/97) manual achievements and completion | [#209](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/209) | #205 |
| [#98](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/98) name provenance | [#210](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/210) | #205 |
| [#99](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/99) matching and review queue | [#213](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/213) | #205 |
| [#100](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/100) Playnite | [#212](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/212) and extension [#2](https://github.com/obsoletelabs/unnamed_tracking_playnite/pull/2) | #205 |
| [#101](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/101) UI | [#214](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/214) | #213 (with #97, #98 and #100 merged in) |
| [#102](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/102) migration | [#216](https://github.com/obsoletelabs/unnamed_tracking_app_2/pull/216) | #214 |
| [#103](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/103) this page | the pull request that adds it | #216 |

## 3. Scenario matrix as executable tests

All in `tests/test_game_identity_acceptance.py` unless another file is named. They share the
fixtures of the earlier phases (`tests/test_playnite_observations.py`,
`tests/test_game_identity_matching.py`, `tests/test_game_names.py`). Playnite is exercised with a
real `utk_` key, the original create/update routes and the `/api/playnite` evidence routes;
Steam, which the host reads server-side, is recorded through the same ingestion service its
sync uses; every user action goes over HTTP.

| # | Matrix row | Test | Asserts |
| --- | --- | --- | --- |
| 1 | Playnite + manual Minecraft merged as one counter, repeated sync | `test_1_playnite_and_manual_merge_into_one_counter_across_resyncs` | Safe default is two counters; after the user groups them the total is one reading (100 h, not 200 h), stays one counter through repeated and newer syncs, and no game, observation or link is created. |
| 2 | Steam Minecraft as a separate counter | `test_2_steam_counter_stays_separate_and_the_umbrella_sums_independent_counters` | The umbrella is the merged Playnite/manual counter (100 h) plus Steam's own (30 h) = 130 h, whichever member is asked. |
| 3 | Split restores everything | `test_3_split_is_reversible_and_recovers_source_data_and_metrics` | After unmerge each side shows its own 100 h and 30 h, rows are identical to before, and the pair can be merged again. |
| 4 | Java/Bedrock as versions, separate then grouped | `test_4_java_and_bedrock_display_separately_then_grouped_without_losing_tracking` | `library_version_display` `separate` then `grouped`, and a per-relationship `link`; per-version playtime (40 h, 15 h) and the umbrella total (55 h) never change. |
| 5 | User-defined relationship between unrelated games | `test_5_user_defined_relationships_follow_the_configured_display` | Default, per-kind rule (`nest`) and per-edge (`hidden`) display; the games stay independent for tracking; ending the relationship removes it. |
| 6 | Automatic merge, undo, rejection, third record | `test_6_automatic_merge_can_be_undone_and_is_not_proposed_again`, `test_6_rejecting_a_suggestion_is_final_but_a_third_record_is_reviewed` | Exact evidence merges and shows a banner with reason and evidence; undo splits and is never proposed again; a rejected pair is not suggested again, and a third record is compared against both. |
| 7 | Placeholder rename | `test_7_placeholder_name_is_replaced_and_tracking_and_source_ids_are_kept` | `Steam App 620` becomes `Portal 2`; playtime, status, external ID and game ID are unchanged; a later placeholder cannot undo it; provenance shows it; one game. |
| 8 | Stable Playnite ID, new legitimate name | `test_8_stable_playnite_id_rename_is_reviewed_without_a_duplicate` | A locked name raises one review (not a duplicate game); a create retry returns the same game; accepting the review renames it; the Playnite GUID and the one observation are unchanged. |
| 9 | Manual achievements and completion survive sync | `test_9_manual_achievements_and_completion_survive_steam_and_playnite_sync` | Three rounds of Playnite status/achievement reports and Steam achievement replacement leave the hand-set `MASTERED`, the manual achievement and its date. |
| 10 | "Completed on 1 of 2 versions", union, per-version lists | `test_10_completion_and_achievements_across_versions_never_double_count` | Summary text, a union of 3 (not 5), per-version lists `(2,1)` and `(3,2)`, repeat read identical. |
| 11 | Disconnect, partial and failed imports | `test_11_disconnect_and_partial_imports_keep_history` | A rejected version, a rejected entry, a one-entry page and an unknown library change nothing; a complete inventory only flags the absent entry; disconnect keeps all rows and the last known playtime. |
| 12 | Populated migration, repeat, then Playnite sync | `test_12_backfill_of_a_populated_library_is_repeatable_and_playnite_keeps_working`; also `test_game_identity_backfill_snapshot.py`, `test_game_identity_migration_upgrade.py`, `test_game_identity_backfill_playnite.py` | Titles, playtime, status and notes identical after the backfill and two more runs; merge, manual achievement and completion kept; a following Playnite sync extends the same games. The upgrade tests run Alembic against a populated schema. |
| 13 | User isolation and permissions for every route | `test_13_no_identity_or_manual_tracking_route_reaches_another_users_data` | Every method of every `/api/game-identity` and `/api/playnite` route is called by a second user with the first user's IDs (404/409/422, never 2xx), the first user's whole state is identical afterwards, and unauthenticated calls are refused. The test **fails if a route exists that it does not list**, so a new route cannot ship without an isolation check. |
| 14 | Search: umbrella, exact edition, similar names | `test_14_search_reaches_umbrella_exact_edition_and_separate_namesakes`; front end: `gameLibraryQuery.test.ts`, `gameIdentityGroups.test.ts` | "Minecraft" finds all four records, "Bedrock" only the Bedrock edition (grouped under Java but still reachable), "Minecraft Dungeons" only itself; Dungeons and Legends are never folded into the umbrella. |

Operations tests, same file: `test_concurrent_identical_playnite_deliveries_make_one_record`
(12 simultaneous identical batches), `test_concurrent_creates_of_one_playnite_id_return_one_game`
(8 simultaneous creates), `test_concurrent_merge_split_and_sync_end_in_one_consistent_state`
(10 merge/unmerge flips racing 6 syncs: no 5xx, nothing created or lost, the total equals the
reconciled sum for whichever state won) and `test_logs_name_who_decided_what_and_never_quote_library_content`.

Scale tests, `tests/test_game_identity_scale.py`: reads cost the same statements and load the
same number of `Game` rows for 40 and 160 games; the matcher costs statements only for new
decisions and a repeat is O(1); a Playnite page costs a constant number of statements; the
backfill costs the same statements for 40 and 160 games.

### Behaviour the tests pin that is easy to misread

- **Two sources are two counters until the user says otherwise** (counters rule 3). Merging
  Playnite and a manual record makes them one *game*; grouping their playtime counters makes
  them one *counter*. Test 1 does both, as a user does.
- **Freshness has one-second resolution.** Two reports of one metric made in the same second
  tie, and the tie is broken by observation ID. That is deterministic but arbitrary, so test 1
  gives Playnite an explicit priority instead of relying on timing. A user who cares which
  source wins should set a priority (`PUT /counters/{id}/members/{observation}/priority`).
- **A Steam sync adopts a game with the same title and source.** `source` is the library the
  record came from; a Playnite entry whose library is "Steam" and a Steam sync of the same
  title are the same record by design. The scenarios use a Playnite entry from another
  library (Microsoft Store) to model a separate Steam copy.

## 4. Test and check results

Environment: Python 3.12 virtualenv `/opt/utapp/venv`, PostgreSQL 16 on localhost, a fresh
database per run (`alembic upgrade head` first), 4 shared CPUs.

| Check (from `src/backend` unless noted) | Result |
| --- | --- |
| Full backend suite on the base (#216) before any change: `pytest -q -p no:cacheprovider` (fresh database) | 2,976 passed, 4 skipped, **2 failed**: `tests/test_plugin_upload.py::test_catalog_entries_accept_explicit_source` and `::test_upload_url_and_catalogue_converge_on_one_commit_path`, both in `/opt/utapp/baseline-failures.txt` (the sandbox has no outbound DNS). No other base failure. |
| Full backend suite with this change, same command, fresh database | 2,999 passed, 4 skipped, the **same 2 sandbox failures**. The 23 new tests are 19 in `test_game_identity_acceptance.py` and 4 in `test_game_identity_scale.py`. Hosted CI has network access and is the source of truth for those two. |
| New and affected suites, repeated three times for the acceptance file | pass every time (the concurrency tests were run repeatedly for flakiness) |
| `ruff format --check .`, `ruff check .` | clean |
| `mypy --config-file pyproject.toml src` | no issues in 389 files |
| `pylint --rcfile=pyproject.toml src` | 9.98/10 over the whole tree. Nothing is reported in a line this change adds. Reported and **already present on the base**: `R0914` in `routes/games.py::update_game`, one 121-column line in `models/game_identity_tracking.py`, `R0801` in `plugin_api` and, only in a whole-tree run, `E1136` in `models/anime.py` (confirmed by running the same files on the base checkout). They belong to other pull requests in the stack and were left alone to avoid conflicts. |
| `alembic upgrade head` on an empty database; `downgrade -1`; `upgrade head`; `alembic check` | single head `c4e8a1f6b3d7`; no model drift |
| CI "Alembic migration graph" step (extracted and run) | `Loaded 59 Alembic revisions`, one head |
| `python3 tools/ci_checks.py backend-size` | passes |
| Front end (`src/frontend`): `npm run lint`, `npm run format`, `npm run typecheck`, `npm run test`, `npm run build` | all pass; 114 test files, 663 tests |
| `mkdocs build --strict --config-file wiki/mkdocs.yml` | builds, no warnings |

Not run here, and left to hosted CI: the Docker image build and the production runtime smoke
test (they need network access and a container runtime), the plugin-integration workflow (it
needs the plugin repository, which is not available in this environment), and the Playnite
extension's .NET build and tests (no `dotnet`).

## 5. Defects found and fixed

| Finding | Evidence | Fix |
| --- | --- | --- |
| Two simultaneous `POST /api/game/create` for one Playnite ID: the loser's recovery path failed (`MissingGreenlet`) instead of returning the winning game. | `test_concurrent_creates_of_one_playnite_id_return_one_game` failed with an unhandled exception. | `routes/games.py` read `current_user.id` after the rollback had expired it; it is now read once at the top. |
| Library layout and overview loaded **every** game of the user as ORM rows to look up a few. | `cProfile` of the overview at 3,000 games: `_alive_games` 1.1 s of 1.45 s. | `features/game_identity/overview.py` loads only the group (overview, sources) or two columns of the games that take part in a merge or relationship (layout). |
| The review queue and the "merged automatically" banner built candidates for the whole library to show a handful of pairs, and built every entry before cutting the page. | 0.6 s each at 5,000 games. | `review._candidates(only=...)`; the queue stops after one more than the page. |
| The matcher re-read the merge graph and re-took the lock for every pair and rebuilt the graph after each merge: 6,086 statements and 28 s for one run at 5,000 games. | `cProfile`; `test_the_matcher_costs_per_new_decision_and_nothing_for_a_repeat`. | One lock for the run, the graph updated in memory, pairs already suggested by the current rule skipped without a statement. |
| The backfill reconciled counters and placed umbrellas one game at a time: 116 s and 87,161 statements at 5,000 games, and an idle re-run 83 s and 74,236. | Profile section 6; `test_backfill_statements_do_not_grow_per_game`. | Sets of games share statements (`counters.sync_many`, `stages.placement`); the results are identical. |
| The library list sorted all of a user's rows for each page. | `EXPLAIN` showed an index scan of 20,000 rows plus a top-N sort. | Migration `c4e8a1f6b3d7`, partial index `ix_games_user_sort_title_live (user_id, sort_title, id) WHERE deleted_at IS NULL`. |
| Identity operations wrote **no log lines** at all. | `grep logger src/features/game_identity*` found none. | Decisions, matching runs, Playnite syncs and backfill failures now log provenance (section 7). |

## 6. Performance

### Method

`tools/profile_game_identity.py` seeds a library through the services the application uses
(ingestion, merge decisions, achievements), then times the hot routes in-process
(`httpx.ASGITransport`, median of three unless noted) and counts SQL statements per request.
It also runs the migration of a games-only library and `EXPLAIN (ANALYZE, BUFFERS)` for the
queries behind the hot paths.

```bash
cd src/backend && export PYTHONPATH=$PWD
source /opt/utapp/env.sh issue_103          # any scratch database
alembic upgrade head
python ../../tools/profile_game_identity.py --games 5000  --explain --out profile-5000.json
python ../../tools/profile_game_identity.py --games 20000 --explain --out profile-20000.json
```

The library is realistic rather than flattering: half Steam and half Playnite, a quarter of
the titles owned in both (as two records, so they are real duplicates), a quarter of those
already merged by the user, the rest of the pairs split 40 % identical evidence (the matcher
merges them), 30 % similar (queued for review) and 30 % title only (left alone), 20 % of games
with ten achievements, and a second user with a tenth as many games alongside. Timings
are one process on a shared 4-CPU machine with a pure-Python PostgreSQL driver: read them as
relative, and rely on the statement counts, which are exact.

Each cell is **median time / SQL statements**. "Before" is the code of pull request #216
(`a272ef27`) run with the same tool on a fresh database; "after" is this change. Libraries:
5,000 games (5,000 games, 5,000 source records, 987 duplicate pairs, 246 merged, 979 games with achievements) and 20,000 games (20,000 games, 20,000 source records, 3,982 duplicate pairs, 995 merged, 4,004 games with achievements). Seeding the 20,000-game library
took 127 s through the real services (ingestion 37 s,
995 merges 56 s).

| Request | 5,000 games before | 5,000 after | 20,000 after |
| --- | --- | --- | --- |
| `GET /api/game/list` | 88 ms / 2 | 93 ms / 2 | 59 ms / 2 |
| `GET /api/game/list` (second) | 68 ms / 2 | 95 ms / 2 | 57 ms / 2 |
| `GET /api/game/list` (second) | 22 ms / 2 | 18 ms / 2 | 29 ms / 2 |
| `GET …/library` | 1.3 s / 16 | 34 ms / 6 | 93 ms / 6 |
| `GET …/games/{id}/overview` | 1.4 s / 70 | 146 ms / 57 | 313 ms / 57 |
| `GET …/games/{id}/overview` (second) | 1.5 s / 66 | 197 ms / 57 | 241 ms / 57 |
| `GET …/games/{id}/sources` | 1.4 s / 57 | 115 ms / 48 | 200 ms / 48 |
| `GET …/games/{id}/metrics` | 139 ms / 39 | 90 ms / 39 | 184 ms / 39 |
| `GET …/games/{id}/metrics` (second) | 153 ms / 39 | 88 ms / 39 | 208 ms / 39 |
| `GET …/games/{id}/tracking` | 71 ms / 13 | 37 ms / 13 | 75 ms / 13 |
| `GET …/games/{id}/achievements` | 33 ms / 8 | 20 ms / 8 | 34 ms / 8 |
| `GET …/decisions` | 20 ms / 1 | 16 ms / 1 | 18 ms / 1 |
| `GET …/names/reviews` | 8 ms / 1 | 5 ms / 1 | 7 ms / 1 |
| `GET …/review/queue` | 635 ms / 5 | 12 ms / 3 | 26 ms / 3 |
| `GET …/review/automatic` | 668 ms / 3 | 4 ms / 1 | 4 ms / 1 |
| `GET …/backfill/plan` | 774 ms / 20 | 65 ms / 20 | 131 ms / 20 |
| `GET /api/game-duplicates` | 277 ms / 2 | 481 ms / 2 | 1.4 s / 2 |
| `POST …/review/run - first run (decides new pairs)` | 38.7 s / 6,086 | 6.0 s / 3,566 | 25.6 s / 14,382 |
| `POST …/review/run - repeat run (nothing new)` | 18.9 s / 2,220 | 1.3 s / 6 | 3.3 s / 6 |
| `GET …/review/queue` (second) | 686 ms / 5 | 173 ms / 5 | 924 ms / 5 |
| `GET …/review/automatic` (second) | 598 ms / 3 | 22 ms / 3 | 26 ms / 3 |
| `POST /api/playnite/observations` | 584 ms / 13 | 646 ms / 13 | 581 ms / 13 |
| `POST /api/playnite/inventory` | 348 ms / 3 | 346 ms / 3 | 1.2 s / 3 |
| `POST …/unmerge` | 54 ms / 8 | 65 ms / 8 | 230 ms / 8 |
| `POST …/merge` | 52 ms / 9 | 59 ms / 9 | 430 ms / 9 |
| `CONCURRENT 20 overview + 5 library + 5 playnite batches` | 40.6 s / 1,450 | 5.9 s / 1,220 | 18.8 s / 1,220 |
| `BACKFILL first run (does the work)` | 107.5 s / 87,160 | 33.3 s / 991 | 126.0 s / 3,601 |
| `BACKFILL second run (nothing left to do)` | 85.4 s / 74,236 | 17.9 s / 783 | 64.6 s / 2,763 |

How to read it:

- **Statement counts are the reliable column**; times vary by about a third between runs on
  this shared machine (for example `/api/game/list` and `playnite/observations` did not change
  and still differ run to run). A request's statement count is the same at 5,000 and 20,000
  games, which is what `tests/test_game_identity_scale.py` pins.
- The identity views were slow because of **how much of the library they loaded**, not because
  of the number of statements: layout 1.3 s to 34 ms, queue 635 to 12 ms, banner 668 to 4 ms,
  overview 1.4 s to 0.15 s at 5,000 games.
- `GET /api/game/list` returns 200 rows per page (the route's maximum) whatever the library
  size. `GET /api/game-duplicates` (the older duplicates list) is a single pass over the
  library's compact rows and is linear in it: 0.3 to 0.5 s at 5,000 and 1.4 s at 20,000 games.
  It is called when the duplicates panel opens, not per game, and was left unchanged.
- The **first matching run** still costs a few statements for each pair it decides (3,566 at
  5,000 and 14,382 at 20,000 games); a repeat is 6 statements.
- `BACKFILL` is the migration of a games-only library (no source records yet). Statements fell
  from 87,160 to 991 at 5,000 games; the remaining time is per-game Python work, linear in the
  library (about 6 ms per game for the first run). A repeat with nothing left to do still
  visits every game (about 3 ms per game).

### What the `EXPLAIN` output showed

`EXPLAIN (ANALYZE, BUFFERS)` at 20,000 games (`--explain`; full plans are in the tool's output):

| Query | Plan | Time |
| --- | --- | --- |
| Library list page (live games by title, 200 rows) | `Index Scan using ix_games_user_sort_title_live`; **before** the index: `Index Scan using ix_games_user_id` over 20,000 rows plus a top-N sort | 0.31 ms (was 9.4 ms) |
| Library title search (`ILIKE '%...%'`) | bitmap scan of the user's rows (a trigram index would not be used by a prefix-less pattern without `pg_trgm`; not added) | 15.7 ms |
| Active same-game edges of a user | sequential scan of 2,209 rows (a small table; the unique `(user_id, kind, label, ...)` index is available as the table grows) | 0.4 ms |
| Observation links of one game | `ix_game_source_observation_links_game_id` | 0.02 ms |
| Ingest lookup by source record | `uq_game_source_observations_record` | 0.05 ms |
| Counters of one game | `ix_game_metric_counters_game_metric` | 0.02 ms |
| Open automatic suggestions (1,773 rows) | `ix_game_identity_decisions_current_outcome` | 2.0 ms |
| Current decision of one pair; rejections between two groups | `ix_game_identity_decisions_second_game_id`; `..._current_outcome` | 0.02 ms |
| Provider achievements of one game | `ix_achievements_game_id` | 0.02 ms |
| Current name provenance | `uq_game_field_values_current_game` | 0.04 ms |

Every hot query except the library list page already used an index; that one, and only that
one, got a new index (migration `c4e8a1f6b3d7`, additive, single head on `a3c5e7b9d102`, verified
with `alembic upgrade head`, `downgrade -1`, `upgrade head` and `alembic check`). The costs in
the table above are therefore in the Python that follows the queries (building objects, walking
the merge graph), which is where the changes in section 5 went.

### Concurrency

- The application is one asyncio process per worker; 30 simultaneous requests on one library
  (20 overviews, 5 layouts, 5 Playnite pages of 50) complete without error and are CPU-bound,
  so wall time is roughly the sum of their individual times (5.9 s for the 30 at 5,000 games and 18.8 s at 20,000, down from 40.6 s before the changes at 5,000).
- Reads of one game's identity views (`overview`, `sources`, `metrics`) reconcile counters
  lazily, which takes the per-user identity advisory lock. Reads by *one user* therefore queue
  behind each other and behind a matching run or merge by that user; other users are not
  affected, and ingestion (Playnite and Steam syncs) does **not** take this lock.
- A matching run holds that lock for its whole transaction. It runs after every complete
  library import (`record_complete_inventory`) and from `POST /review/run`. The first run on a
  very large unmatched library is the long one (6.0 s at 5,000 games and 25.6 s at 20,000 on the profiled machine); a repeat is a few statements.

## 7. Operations

### Transactions, retries and concurrent sync

- Every identity change (merge, unmerge, separate, move, group, adjust, matching) takes the
  user's advisory lock for the transaction (`graph.lock_user_identities`), so concurrent
  requests serialise instead of interleaving; a failed request rolls back completely.
- Ingestion is idempotent by `(connection, external id)`: a retried or duplicated delivery
  reports `unchanged`, an older one `stale`; a rejected batch changes nothing (tests 11 and the
  concurrency tests).
- A complete inventory is the only thing that marks a record unavailable, and it never deletes.
- The backfill is resumable and takes its own per-user lock; a second concurrent start is
  refused (409 with `wait=true`; a background start simply does not run twice).

### Logs and diagnostics

Logger names are the module paths (`src.features.game_identity.decisions`, `...review`,
`src.features.game_ingestion.playnite`, `src.features.game_identity_backfill.runner`). Lines
carry IDs and counts only; titles, source IDs, Playnite GUIDs and credentials are never
written (tested with a distinctive title and GUID).

| Line | Fields |
| --- | --- |
| `identity decision` | user, decision ID, subject, outcome, actor kind (`user`, `automatic`, `migration`, `source`), rule version, ID of the decision it replaced |
| `identity matching` | user, games evaluated, associated, suggested, skipped, whether automatic merging and a complete inventory applied, seconds |
| `playnite observations` | user, library, entries, outcome counts (`updated`, `unchanged`, `stale`, `unlinked`, `in_trash`) |
| `playnite inventory` | user, library, entries reported, applied, marked unavailable |
| `identity backfill failed` | user, stage, exception class, SQLSTATE (never the exception text) |
| `identity backfill finished` | user, outcome, number of findings |

The durable provenance is in the database and is readable per game without a shell:
`GET /api/game-identity/decisions` (who decided what, rule, confidence, evidence, what it
replaced), `GET /api/game-identity/games/{id}/sources` (observations, revisions, relationships,
counters), `GET /api/game-identity/names/games/{id}` (name history) and
`GET /api/game-identity/backfill` (stages, counts, error codes).

### Troubleshooting

| Symptom | Likely cause and what to check |
| --- | --- |
| Two rows for one game after a Playnite sync | They are two records: a Playnite entry and a manual or Steam record are never merged on a title alone. Run **Review** (`POST /api/game-identity/review/run`); a strong match is merged and announced, a weaker one is queued. A pair marked "keep separate" is never suggested again. |
| Playtime looks doubled | Two sources are separate counters by default. Merge the games, then group the two playtime counters (`POST /counters/{id}/group`), or set a priority. |
| Playtime did not follow a new Playnite value | An older or equal `modified_at` reports `stale`; a same-second tie goes to the lower observation ID. Set a priority, or check `GET /games/{id}/metrics` for the selected source and the losing readings. |
| A game kept its old name | A name the user set is locked and a Playnite rename becomes a review (`GET /api/game-identity/names/reviews`), not a rename. |
| A record shows "unavailable" | A *complete* Playnite inventory did not list it. It is flagged, not deleted; the next report restores it. |
| Playnite says "unsupported contract version" | The extension and host differ; `GET /api/playnite/contract` lists the supported versions. |
| Backfill status `failed` or `incomplete` | `GET /api/game-identity/backfill` shows the stage and an error code (no library content). Fix the cause and start it again: it resumes. See [migration](game-identity-migration.md). |
| Matching or an overview is slow for one user | See section 6: first matching run on a big library; the identity lock queues one user's reads behind it. |

### Where the rules are written down

Schema concepts: [data model](game-identity-model.md). Decision rules:
[decisions](game-identity-decisions.md), [counters](game-identity-counters.md),
[matching](game-identity-matching.md), [names](game-identity-names.md),
[manual tracking](game-identity-manual.md). Playnite contract:
[Playnite observations](playnite-observations.md) and the
[Playnite integration page](../integrations/playnite.md). Migration:
[migration](game-identity-migration.md). Interface: [UI](game-identity-ui.md).

## 8. Playnite

- **Host contract** (all passing, section 4): `tests/test_playnite_contract.py`,
  `tests/test_playnite_observations.py`, `tests/test_game_identity_backfill_playnite.py` and
  the scenarios above exercise the endpoints the extension calls with a real user API key:
  create (idempotent, concurrent-safe), update with user locks, `/api/playnite/contract`,
  `/observations`, `/inventory`, an existing connection continuing after the backfill and
  previously imported IDs.
- **Extension (C#)**: not built or run here. `dotnet` is not installed in this environment.
  The extension changes live in `obsoletelabs/unnamed_tracking_playnite` pull request
  [#2](https://github.com/obsoletelabs/unnamed_tracking_playnite/pull/2) (a draft for that
  reason). Before it is merged its CI (`build.yml`, `build-pext.yml`) and
  `tests/Companion.Tests`, `validate-extension.ps1`, `validate-package.ps1` must pass.
  This change does not touch that repository.

## 9. Known limitations

- Statement counts do not grow with the library, but several paths are still linear in
  *CPU*: the library layout and the duplicates list read every merge edge or compact game row
  (section 6 shows the cost at 20,000 games), and the library list is paged at 200 rows per
  request, so a front end that loads a 20,000-game library makes 100 requests.
- The first matching run on a large unmatched library is slow (section 6) and holds the
  user's identity lock throughout; it runs inside the import transaction after a complete
  inventory. It could be moved to a background job if a deployment shows this to matter.
- `GET .../metrics`, `overview` and `sources` take the per-user lock and may create counters
  (lazy materialisation, #96), so they are not free of writes.
- Same-second reports tie on freshness (section 3).
- Title search is a substring `ILIKE` over the user's rows (about 14 ms at 20,000 games); a
  trigram index was considered and not added.
- The hosted jobs that need Docker, the production runtime smoke test and the strict MkDocs
  build with the repository's pinned plugins were not run in this sandbox.
