# Manual achievements and completion

Phase 3 of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#97](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/97). It builds on the
[data model](game-identity-model.md), [ingestion](game-ingestion.md),
[decisions](game-identity-decisions.md) and [counters](game-identity-counters.md). Paths start
`src/backend/src/`. No migration: `game_manual_achievements` and `game_field_values` were created by
`716a1e2c451d` (#93).

## 1. Rules

1. **Manual is the user's.** A manual achievement lives in `game_manual_achievements`, which no sync
   writes. Provider achievements stay in `achievements` and are upserted by sync as before.
2. **Identity of an achievement is never a title.** A provider achievement is `(provider, external_id)`:
   two provider rows with the same name but different IDs stay two, while the same Steam achievement on two
   merged rows (a duplicated source observation) counts once. A manual achievement is its own entry unless
   the user gave several the same `match_key`, which is the explicit relationship across versions. Manual and
   provider achievements never merge, even with equal titles.
3. **A hand-set status is protected.** `set_completion` writes a `completion_status` field value
   (origin `user`, locked, the superseded rows are the history), sets `games.status` and adds `status` to
   `games.locked_fields`.
4. **Nothing is deleted.** Removing a manual achievement sets `archived_at`; a superseded completion stays.

## 2. Where sync is stopped

| Path | Behaviour for a locked status |
| --- | --- |
| API-key `PATCH /api/game/update/{id}` (Playnite) | `status` and `completion_date` are dropped from the update; the report is still recorded as the source's observation; other fields apply |
| `apply_provider_progress` (Steam, PlayStation, RetroAchievements sync) | returns without moving the status |
| Interactive `PATCH /api/game/update/{id}`, bulk update | a changed status is recorded as a manual completion, so it is protected from then on |
| `DELETE /api/game-identity/games/{id}/completion` | releases the lock; imports may update the status again |

Steam and Playnite achievement syncs replace only `achievements` rows, so manual rows and their earned state
survive any number of syncs.

## 3. Aggregation

The "umbrella" is the merge group of a game (`same_game`, #95) plus any manual achievement attached to the
umbrella of one of its identities (`scope=umbrella`, needs a `game_identities` placement from #102). Merging
widens the view, splitting narrows it; no row is rewritten.

- `umbrella.total` / `unlocked` count distinct entries (rule 2); an entry is unlocked when any version has it.
- `versions[]` gives each tracked version its own provider and manual counts, so per-version totals can add up
  to more than the umbrella total when versions share an achievement.
- Completion: `completed` of `of` tracked versions (a version is completed when its status is completed, beaten or
  mastered, `policy.is_completed`), with `summary` ("Completed on 1 of 2 tracked versions"), `partial` and
  `all_completed`. `user_set` says whether the status is the user's.
- `GET /api/game-identity/games/{id}/metrics` (#96) uses the same aggregation for `achievements` (`exact: true`,
  `versions`) and returns `completion.tracked`.

## 4. API (signed-in user; another user's IDs are 404)

| Route | Purpose |
| --- | --- |
| `GET /api/game-identity/games/{id}/tracking` | Completion and achievements together |
| `GET .../games/{id}/achievements` | Umbrella totals, per-version breakdown, the manual list |
| `POST .../games/{id}/manual-achievements` | Create `{title, description?, icon_url?, category?, position?, match_key?, achieved?, achieved_at?, scope: version/umbrella}` |
| `PATCH .../manual-achievements/{id}` | Edit; unsent fields are untouched; a blank title or invalid icon is 422 |
| `PUT .../manual-achievements/{id}/earned` | `{achieved, achieved_at?}`; earning without a time records now, unearning clears it |
| `DELETE .../manual-achievements/{id}` | Archive (204) |
| `GET .../manual-achievements/{id}/icon` | Local resized copy through the provider-icon fetcher (public hosts only) |
| `GET/PUT/DELETE .../games/{id}/completion`, `GET .../completion/history` | Per-version status, set by hand, release, history |

Icons must be public `http(s)` addresses without credentials, on ports 80/443; they are fetched, size-limited and
resized on first display, like provider icons.

## 5. Export and history

`GET /api/export/library` (and the scheduled backups, which share the builder) include
`manual_achievements` and `manual_completion` next to the games; older exports without them stay valid
(`format_version` is unchanged because the fields are additive). Only the library import restores games; a
backup file is restored through the same `POST /api/import/library`.

**Restore.** `POST /api/import/library` accepts either the old bare games list (no manual rows) or a whole
export (`{games, manual_achievements, manual_completion, ...}`; other sections are ignored). Each game is created as
before and its exported ID is mapped to the new game; `manual.restore_rows` then recreates the rows on the mapped
games under the importing user:

- a row whose game is not in the file, an umbrella-scoped row, a row failing the same validation as the API (blank
  title, unsafe icon, bad time) or a non-object row is skipped and reported in `errors`; nothing is attached to another
  user's game, since only games created by this request are mappable;
- an achievement equal (title, `match_key`, category, description) to one already on that game, or repeated in the
  file, is added once;
- only the `current` completion of a game is restored, through `set_completion`, so the status is locked as the
  user's; superseded choices (the history of the old server) and removed (archived) achievements are not carried;
- `created_at` is kept, new IDs are generated, and the result reports `manual_achievements` and `manual_completion`
  counts. Importing the same file twice repeats the games as the game import always did; each new copy receives its own rows once.

History of a completion is `GET .../completion/history`; a game's other field edits keep using
`GET /api/game/{id}/field-changes`. The game page's content counts (`/api/game/{id}/content-counts`) include manual achievements.
