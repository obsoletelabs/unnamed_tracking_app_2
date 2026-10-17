# Counter groups and persistent adjustments

Phase 2 of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#96](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/96). It describes the
**code that exists now** on top of the [data model](game-identity-model.md),
[ingestion](game-ingestion.md) and [decisions](game-identity-decisions.md). Paths start
`src/backend/src/`. No migration: `game_metric_counters`, `game_metric_counter_members` and
`game_metric_adjustments` were created by `716a1e2c451d` (#93).

## 1. Rules

1. **Identity and counter are separate.** Who is the same game is decided by #95. Which
   observations report the *same counter* is a separate, explicit grouping.
2. **Same counter: one reading, never a sum.** Observations in one counter are reconciled;
   independent counters are combined by the metric's own rule.
3. **Safe default.** Reading an identity creates one counter per observation and metric it
   reports. Nothing is grouped until a user (or a reviewed rule from #99) groups counters, so
   the default can over-count a game that two sources both report, but a user can fix it with
   one call, and it can never hide a counter.
4. **Unknown is not zero.** A missing or partial observation is skipped; a counter with no
   reported value is unknown, and an offset without a base stays unknown.
5. **A merge changes nothing stored.** Counters live on the `games` row that holds the
   observation; a view over a merged group combines the counters of the group's games.
   Unmerging simply narrows the view, so counters, members and adjustments survive.

## 2. Selection inside a counter (`features/game_identity/policy.py`)

| Metric | Reading used | Across counters |
| --- | --- | --- |
| `playtime` | Lowest member `priority`, then the freshest `field_observed_at['playtime_seconds']`, then observation ID | Sum |
| `last_played` | Largest value (latest trusted report) | Latest |
| `achievements` | Winner as for playtime (unlocked, total from one observation) | Never summed: per-counter breakdown, headline is the maximum. When provider `achievements` rows exist the union by normalised name is used (`exact`) |
| `completion` | Winner as for playtime | "Completed on N of M"; the user's own `completion_status` field value is returned separately |
| `ownership` | Winner as for playtime | Any true |
| `installed` | Winner as for playtime | Not merged: listed per source |
| rating | User-owned field value (`rating`, origin `user`); sources never write it | |

Recency is per metric, so renaming a game never changes which source wins its playtime.
Priority is explicit (`PUT /counters/{id}/members/{observation}/priority`, lower wins, default
100). The result is deterministic for any input order.

## 3. Adjustments

Editing the effective playtime stores an **offset against the selected base**, not a new
value: 200 h edited to 190 h stores -10 h; when the source later reports 210 h the effective
value is 200 h. Details:

- `adjust_total` edits the merged total; the difference goes to one counter (the largest by
  default, or the one named). `adjust_counter` edits one counter. Repeating either is a
  no-op; editing back to the base clears the adjustment. A counter with no reported value
  stores an `override`. The result is never below zero.
- One current adjustment per counter; replaced ones are superseded, never deleted.
- **Rebasing.** When the selected base observation changes (priority change, grouping, the
  base observation moved or split away), the **offset is kept** and applied to the new base:
  the user's correction is a delta from whatever the counter reports. The change is
  recorded (`reason = rebased:<previous base>`, `rebased: true` in the API). When the base
  observation moves to another game its counter is archived and the adjustment is carried to
  the observation's new counter (`carried: true`).
- **Grouping** archives the source counter (rows kept). Its adjustment is copied to the
  target if the target has none; **ungrouping** reopens the original counter and retires the
  copy, so the original view returns exactly.

## 4. API (all scoped to the signed-in user; foreign IDs are 404)

| Route | Purpose |
| --- | --- |
| `GET /api/game-identity/games/{id}/metrics` | Effective value per metric for the identity group with raw member values, selected source, adjustment, and conflict provenance (why each other reading lost). Lazily creates default counters |
| `PUT .../games/{id}/metrics/playtime` | Set effective playtime `{seconds, counter_id?, reason?}` |
| `DELETE /counters/{id}/adjustment` | Remove the correction |
| `PUT /counters/{id}/members/{obs}/priority` | Source preference |
| `POST /counters/{id}/group` / `.../members/{obs}/ungroup` | Same counter / independent again |

Moving an observation (`move_observation`, #95) re-homes its memberships and adjustment in
the same transaction. Grouping counters of games that are not merged is refused (409).

## 5. Not included

Playnite library scope (#100), the matching rules that decide groupings (#99), UI (#101) and
backfill of existing libraries (#102). Library views still read `games`.
