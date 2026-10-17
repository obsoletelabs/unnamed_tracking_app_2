# Migrating an existing library to the game identity model

Phase 6 of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#102](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/102). It describes the
**code that exists now**: an additive, resumable, idempotent backfill that maps every existing
`games` row onto the [data model](game-identity-model.md). Backend paths start `src/backend/src/`.

## 1. What it guarantees

- **No `games.id` changes.** Every game keeps its ID, folder, notes, files, history, achievements
  (provider and manual), collections, wishlist state, children and Playnite GUID. A tracked
  identity's primary key *is* `games.id` (rule 1 of the model), so routes, bookmarks, foreign
  keys, calendar entries, plugin replay IDs and external clients keep resolving. Because no ID
  moves, **no alias or redirect table is needed**; the verification step below proves it.
- **Nothing is dropped, rewritten or deleted.** No column or table is removed
  (`parent_game_id`, `relationship_type`, `game_duplicate_dismissals`, `locked_fields`,
  `external_id`, `playnite_guid`, `achievement_overrides`, ... all stay and keep working). The
  only existing column the backfill writes is `games.locked_fields`, to add `status` for a status
  a person evidently chose (section 4); an existing lock is never removed.
- **Titles never decide identity.** Equal titles, near titles and "looks like a duplicate" stay
  separate. Only what the old schema recorded as the user's decision (an owned-copy group, a
  "keep both" dismissal) is carried forward as a decision.
- **Additive and idempotent.** Each stage adds only what is missing. Running the job twice (or
  after a sync) changes nothing; records a sync or a person already changed are not rewound.
- **Resumable.** Work is committed in bounded chunks together with its counts. A crash, restart
  or failed chunk loses at most the chunk in flight; the next run continues.

## 2. Why a resumable job and not an Alembic data step

The schema change is a normal migration, `a3c5e7b9d102` (single head, on `716a1e2c451d`): it only
adds `game_identity_backfills`, one progress row per user. The data mapping is deliberately
**not** inside Alembic:

- It runs per user in bounded transactions with per-user advisory locks (the same locks sync and
  user edits take), instead of one transaction holding the schema-migration lock for the whole
  library. A large library must not make container start-up time out or roll back the schema.
- It must be interruptible and resumable, report per-stage counts, and be repeatable after a
  failure or after later syncs add rows. A migration revision runs once.
- It reuses the application services (ingestion, decisions, relationship validation, name and
  counter services) so the backfill obeys the same invariants as live writes, which a SQL-only
  data migration would duplicate and drift from.
- A failed backfill leaves the schema upgraded and the application fully working (every reader
  falls back to the legacy columns for a game with no observations), so a backfill problem never
  blocks an upgrade or needs a downgrade.

## 3. Mapping

| Old representation | New representation | Notes |
| --- | --- | --- |
| A standalone `games` row | One `game` umbrella (title of the row) with one `standard` identity placement | Includes Trash and wishlist rows, so restoring from Trash keeps its place |
| An `owned_copy` group (`parent_game_id`) | One umbrella; the main is the primary `edition`, each copy a `copy` identity; a `same_game` relationship and a `confirmed_same` decision by actor `migration` | The user's own earlier grouping. If the person already kept the pair separate with the new tools, that wins and the group is only reported (`owned_copy_kept_separate`) |
| `parent_game_id` with `mod`, `modpack`, `expansion`, `dlc`, `standalone_expansion`, `total_conversion` | A typed relationship (`mod_of`, `modpack_of`, `expansion_of`, `dlc_of`, `standalone_expansion_of`, `total_conversion_of`) from child to parent, origin `migration` | Each is its own umbrella (a DLC is a different game). A cycle or missing parent is reported, not forced. A relationship the user already ended is not recreated |
| `game_duplicate_dismissals` ("Keep both") | `confirmed_separate` decision by actor `migration`, created at the dismissal's own time | Skipped, and counted, when the pair is already decided or now in one merged group |
| `playnite_guid` | Observation on `playnite` / `legacy`, external ID = the GUID | Same connection the live Playnite routes use, so the next sync updates it |
| `external_id` with `source` Steam, RetroAchievements, PlayStation | Observation on `steam` / `retroachievements` / `playstation` / `legacy`, external ID = the row's | Same connection as live syncs. Achievement totals come from the provider's own rows |
| `external_id` with any other `source` (a store plugin) | Observation on plugin `legacy-unattributed` / `legacy` | The plugin ID and account scope are not stored on the row and are **never guessed**. A later plugin import keeps updating the game and records its own scoped observation |
| No GUID and no external ID | Observation on `manual` / `local`, external ID = the game's own ID | Matches what live manual edits record |
| Several rows with the same source record (audit H4) | The oldest live row keeps the record; each other row gets its own observation on a `legacy-duplicate:<game id>` scope, plus an `unresolved` automatic decision (`legacy-duplicate-source-id`) | Nothing is lost, nothing is merged: the pair appears in the review queue |
| `stale_since` | `available: false` on host and plugin observations | Absence was already recorded by an earlier complete pull |
| `playtime_seconds`, `last_played_at`, `owned`/`wishlisted`, achievements | Observation facts; zero playtime, missing last played and placeholder names (`Steam app 12345`) are **left unknown** | The row's value is recorded as the source's last reading; the next sync supersedes it. One counter per observation and metric is created (nothing grouped) |
| `title`, `locked_fields` | One `title` field value with origin `legacy`, quality (placeholders flagged for repair), and a `legacy` lock for an existing title lock | Same provenance the name service writes |
| `status` | A `completion_status` field value of origin `legacy`. A non-Playnite game whose status is not Backlog or Wishlist is **locked** (`lock_origin legacy`, `status` added to `locked_fields`) | Audit H9. Sync can never move such a status, so the lock costs nothing; it protects it from API-key updates. A Playnite game's status stays unlocked because Playnite owns it. Release with `DELETE .../completion` |
| Corroborated title/release/platform candidates | The existing matching job queues review entries with `inventory_complete=False` | Nothing merges: the inventory is not confirmed complete |

Manual achievements and `achievement_overrides` are not touched. Series, collections and
`collections` names stay free text; no umbrella is created from them. Ratings and personal rank
stay on the row (they were never mixed with source data).

## 4. Stages, progress and reports

`features/game_identity_backfill/` (`mapping.py` pure rules, `stages.py`, `runner.py`,
`progress.py`). Stages run in order; a completed stage is skipped when a run resumes.

| Stage | Does | Resume point |
| --- | --- | --- |
| `observations` | connections, observations, links (origin `migration`) | ID cursor |
| `merges` | owned-copy groups | skips merged pairs |
| `placement` | umbrellas and identities | skips placed games |
| `relationships` | typed relationships | skips existing edges |
| `decisions` | dismissals | skips decided pairs |
| `review` | queues shared source records | skips known pairs |
| `fields` | title and status provenance, status protection | ID cursor |
| `counters` | counters and memberships | ID cursor |
| `matching` | review queue for corroborated candidates | idempotent |

Progress lives in `game_identity_backfills` (one row per user): `status` (`pending`, `running`,
`completed`, `incomplete`, `failed`), `stage`, `completed_stages`, per-stage `counts`, `errors`,
`attempts`, timestamps, and `before`/`after` snapshots. **Only counts, stage names, error codes
and game IDs are stored. No title, note or other library content is stored or logged.** Counts
are written in the same transaction as the work, so they never double count after a resume.

Anomalies that do not stop a run are listed as `{stage, code, count, game_ids[<=20]}`:
`observation_invalid`, `owned_copy_kept_separate`, `owned_copy_invalid`, `relationship_invalid`
(for example a cycle), `parent_missing`, `relationship_type_unmapped`. A stage that raises
records `stage_failed:<ExceptionClass>:<SQLSTATE or class>`, keeps all earlier committed chunks,
and marks the run `failed`.

### Verification (before/after counts and invariants)

At the start a run snapshots counts the backfill must never reduce, and at the end compares them:
games, provider achievements, `achievement_overrides`, links, notes, field changes, dismissals,
games with a parent, with a Playnite GUID, with collections, with notes, observations,
decisions, and total playtime. A **decrease** marks the run `failed` (`decreased:<name>`) and is
a bug to report. Coverage gaps (`games_without_identity`, `games_without_source_link`, for
example a game created while the run was going) mark the run `incomplete`: simply run again.
The database also enforces tenancy: composite `(user_id, ...)` keys reject any row pointing at
another user's game, umbrella or observation.

## 5. Running it

The job starts automatically beside the server on container start (`entrypoint.sh`, background,
never delaying or failing start-up). Opt out with `GAME_IDENTITY_BACKFILL=off` and run it when
you choose:

```bash
python -m src.features.game_identity_backfill --dry-run          # coverage numbers, writes nothing
python -m src.features.game_identity_backfill                    # every user; exit 1 unless all completed
python -m src.features.game_identity_backfill --user <user id> --batch-size 200
python -m src.features.game_identity_backfill --reset            # restart a failed run at its first stage
```

A signed-in user can also use `GET /api/game-identity/backfill` (status and counts),
`GET .../plan` (what is left) and `POST .../backfill[?wait=true]` (start or resume; 202).
A second runner for the same user is refused (`BackfillBusy`, 409) by a session advisory lock that
the database releases if the process dies, so a crash never leaves a stuck lock. A status of
`running` with no live process is simply resumed.

### Backup before you run it

The job is additive, but take a backup first, as for any upgrade (audit P9; the JSON export and
scheduled backups do **not** contain identities and are not a rollback):

```bash
pg_dump --format=custom --file=utapp-before-identity-backfill.dump "$DATABASE_URL"
# and copy the data folder (artwork, notes, saves are untouched by the job, but keep the pair consistent)
```

### Failure and retry

| Situation | What happens | What to do |
| --- | --- | --- |
| Process killed, container restarted, database restarted | Committed chunks stay; the chunk in flight rolled back | Start again (automatic on next start) |
| A stage raised | Run is `failed`, `errors` names the stage and the error class | Fix the cause (disk, connection), run again; `--reset` only to restart from stage one |
| Run `incomplete` | New games appeared during the run | Run again |
| Run `failed` with `decreased:...` | An invariant broke | Stop, keep the dump, report it. Restore the dump if you cannot proceed |

## 6. Rollback and forward-recovery limits

Stated plainly:

- **While the new tables are empty or hold only backfilled rows**, the application still reads and
  writes the legacy columns everywhere; rolling the *code* back to a version that predates the
  identity model works, because nothing the old code reads was changed (apart from the extra
  `status` entry in `locked_fields` described above, which an old version honours as a lock).
- **The previous Alembic migration (`716a1e2c451d`) refuses to downgrade once the identity tables
  hold data** (it names them). That is intentional: from the moment a person merges, splits,
  adjusts or records manual tracking, those rows exist nowhere else. `a3c5e7b9d102` itself
  downgrades by dropping only the progress table.
- **To return to the exact pre-backfill state you must restore the `pg_dump` taken before the
  run** (and the data folder from the same moment). Anything done in the application after the
  dump (new games, syncs, decisions) is lost with it. There is no supported "undo the
  backfill" that keeps later work: its rows are indistinguishable from later user work except by
  origin (`migration`) and cannot be removed safely once decisions build on them.
- **Forward recovery** is the normal path: fix the cause and run again. Individual wrong
  outcomes are corrected with the identity tools (unmerge, move an observation, end a
  relationship, release a completion); a merge is never destructive (P1).
- Old columns and tables are retained. Removing them is a separate, reviewed cleanup after every
  consumer has moved.

## 7. Tests

`tests/test_game_identity_backfill.py` (a representative populated library: single-source,
wishlist placeholder, Playnite, manual with locks, owned copies, DLC, dismissals, ambiguous equal
titles, shared source records, plugin and trashed rows, achievements, overrides, links, notes,
collections), run **twice** with identical row counts, plus resume after a failed chunk and a
failed stage, a user decision made before the run, concurrent-runner refusal, user isolation, new
games on a rerun, the dry-run plan, and the invariant judge. `tests/test_game_identity_backfill_snapshot.py` migrates a real
schema from the revision before the identity model, fills it with ~330 mixed games, upgrades to
head, backfills twice, and compares every old column afterwards.
`tests/test_game_identity_backfill_playnite.py` runs the real extension flow over HTTP with a
`utk_` key: Playnite library created before the model, backfill, a full observations sync,
inventory, update with a locked title, and a rerun, asserting one record per GUID and no new game.
