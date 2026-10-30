# Optional AFK-time observations and aggregation semantics

Design proposal for [#264](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/264)
(source: Rosefall #65). **Accepted design; not implemented.** Decisions confirmed on 19 October 2026. Paths start
`src/backend/src/`. Current-state claims were re-checked against host `main` at `07933cd2` and the Playnite
extension at `af054d59` on 30 October 2026.

**Feasibility verdict: phase 1 (host contract and storage) should move to v0.0.5; v0.0.4 closes with this
design.** Phase 1 needs two contract revisions (Playnite and the generic observation contract), a migration for the
interval table, a consent setting, a new plugin capability and delegation scope, AFK-specific adjustment handling
and a retention job, each with tests. No producer can send AFK data today (section 5), so landing the host half in
v0.0.4 would ship storage nothing writes to. The metric column is already a `String(40)`, so no enum migration is
needed and the work stays additive whenever it is scheduled.

## 1. Problem and current state

Users want to know how much recorded playtime was spent away from the keyboard. Nothing in the host defines a
producer, an idle threshold, or how such a value relates to playtime.

What exists today:

* Observations and counters ([Game identity counters](../game-identity-counters.md),
  [Playnite observations](../playnite-observations.md)). `database/models/game_identity_tracking.py` defines
  `GameMetric` (`playtime`, `last_played`, `achievements`, `completion`, `ownership`, `installed`),
  `game_metric_counters`, `game_metric_counter_members` and `game_metric_adjustments`. The counter's `metric`
  column is `String(40)` validated in code, not a native database enum. Rules: unknown is not zero; one counter is
  one reading and never a sum (`policy.pick_preferred`: lowest `priority`, freshest per-field time, then
  observation id); independent counters of one game add up for playtime (`game_identity/playtime.totals`); user
  corrections are offsets or overrides and never edit a source value. `playtime.effective` applies an adjustment
  only when the counter's metric is `playtime`, so another metric needs its own adjustment rule.
* Two versioned contracts, both `contract_version` 1 with `extra="forbid"` (an unknown member is 422):
  * Playnite: `POST /api/playnite/observations` (`features/game_ingestion/playnite.py`, `PlayniteBatchV1`),
    behind the `library.sync` delegated grant or a user key. `PlayniteGameV1` carries playtime, last played,
    installed, owned, favorite, status, achievements, source, developer, publisher, series and `modified_at`
    (the stale-replay guard). `SUPPORTED_VERSIONS = (1,)`; another version is 400
    `unsupported_contract_version`.
  * Generic: `features/game_ingestion/contract.py` (`ObservationBatchV1`, `ObservedFacts`), used by host
    ingestion and plugin imports. `ObservedFacts.playtime_seconds` is the only duration.
* Plugins: the gateway method `games.import` (`plugin_api/game_import.py`) needs the `games.write` capability and
  accepts `playtime_seconds` per game, recorded as an observation. `plugin_api/game_library.py` is read-only. The
  Plugin API v1 has no session start/stop or duration-interval method.
* Delegated app grants (`api/schemas/delegation.py`) use dotted scopes such as `library.sync`; user API keys only
  carry `read`, `write` and `admin` (`core/auth.py`, `API_KEY_SCOPES`).
* No code in the host, the Playnite extension or the plugin repository measures idleness.

## 2. Goals and non-goals

Goals: an **optional, off by default** metric `afk_time` in seconds, ingested through the same observation and
counter machinery, shown separately from playtime, with explicit and reversible user control.

Non-goals (enforced, not only documented): raw keyboard or mouse capture; per-key, per-window or screenshot data;
inferred activity from other signals; **subtracting AFK from stored playtime** or changing how `playtime` is
selected; a host-side idle detector. Collection belongs in the companion and plugin repositories.

## 3. Proposed data model (additive)

Reuse the counter tables for cumulative totals and add a session-interval table for bounded history.

* Add `AFK_TIME = "afk_time"` to `GameMetric` (a Python `StrEnum`, validated in code per its docstring). The
  metric columns are `String(40)`, so this needs no schema change.
* Observation facts carry `afk_seconds` (integer >= 0) and `afk_threshold_seconds` (the producer's idle
  definition, e.g. 300) next to `playtime_seconds`. A reading with a different threshold is a different counter
  and is never grouped automatically.
* `game_afk_intervals(observation_id, session_id, started_at, ended_at)`, capped at 1 000 rows per
  observation, stores per-session intervals for 90 days. Deduplicate overlap within the same session/counter;
  do not sum intervals into an already cumulative reading. Pruning intervals never reduces cumulative totals.
* Consent: a per-user setting `afk_collection_enabled` (default false) in the existing preferences store
  (`user_preferences.data`, JSONB), and a separate grant for the producer: a delegated scope (proposed
  `library.afk_sync`, beside `library.sync`) for the Playnite extension and a plugin capability for plugins. User
  API keys keep their coarse `read`/`write` scopes; for them the per-user setting is the gate.
* AFK corrections use the existing `game_metric_adjustments` rows (`offset` or `override`); the adjustment rule
  currently limited to `playtime` in `playtime.effective` is generalised to `afk_time`.

## 4. Versioned contract

`contract_version` is raised additively to 2 on both contracts; `SUPPORTED_VERSIONS` becomes `(1, 2)` and v1
stays valid and unchanged. Proposed v2 fields, all optional:

| Field | Where | Type and unit | Bounds |
| --- | --- | --- | --- |
| `afk_seconds` | `PlayniteGameV2`, `ObservedFacts` (v2) | integer, seconds, cumulative for the record like `playtime_seconds` | 0 to 2 000 000 000 |
| `afk_threshold_seconds` | same | integer, seconds of no input before time counts as AFK | 60 to 3 600; required when `afk_seconds` is sent |
| `afk_intervals` | same | list of `{session_id, started_at, ended_at}`; `session_id` a producer-local string up to 128 characters, times Unix seconds UTC | at most 1 000 per entry; `started_at < ended_at` |

Session intervals require a stable producer-local session ID and boundaries inside that session. Rules:

* Omitted means unknown, never zero. `0` is a real "present the whole time".
* `afk_seconds` greater than the same entry's `playtime_seconds` is 422. It is not otherwise validated against stored playtime.
* Unless the user setting is on, the host does not store the field and answers with an explicit
  `afk: "disabled"` outcome so the producer can show why.
* A v2 payload sent to a host that only supports v1 gets the existing 400 `unsupported_contract_version`; the
  producer reads `GET /api/playnite/contract` first and sends v1 (no AFK) to an older host.
* Plugins need a new capability, proposed `games.afk.write` to match the existing dotted `Capability` names
  (`games.write`), following [Plugin permissions](../plugin-permissions.md) (declared, user-granted,
  gateway-enforced). `games.import` with AFK fields but without it is rejected. The capability is documented in
  [Plugin capabilities](../plugin-capabilities.md) when implemented.

## 5. Producer capabilities and gaps

| Producer | Today (verified) | Gap |
| --- | --- | --- |
| Playnite extension (`obsoletelabs/unnamed_tracking_playnite`) | Sends contract v1 (`PlayniteObservations.cs`, `Version = 1`) with Playnite's own cumulative playtime and the other fields in section 1; can sync on `OnGameStopped`. No idle sampling. | Opt-in idle sampler using OS idle time only (no input content), session intervals, contract v2 negotiation and the `library.afk_sync` grant |
| Desktop companion | Does not exist. (The extension's `Companion.Tests` project tests the Playnite extension.) | A separate product and repository using the same contract; not planned here |
| Plugins via Plugin API v1 | `games.import` under `games.write` can report `playtime_seconds` | `games.afk.write` capability and AFK fields on `games.import` (host); any collecting plugin lives in `obsoletelabs/unnamed_tracking_app_plugins` |
| Host | No AFK field, metric, setting or table | Owns only the contract, storage, consent, enforcement and display |

## 6. Aggregation semantics

`afk_time` selects like `playtime` inside a counter (lowest `priority`, freshest `field_observed_at`, then
observation id) and **sums across independent counters**.

| Case | Result |
| --- | --- |
| Same observation re-sent (duplicate) | Idempotent `unchanged`; a stale `modified_at` is ignored |
| Two sources as independent counters (PC 3 600 s, Deck 1 200 s) | 4 800 s with a per-source breakdown |
| The same two readings grouped as one counter | One reading wins by priority then recency: 3 600 s, never summed |
| Different thresholds (300 s vs 600 s) | Not comparable: separate counters, grouping refused with a message |
| Merge of two games | Counters follow the observations; the view combines them; nothing is rewritten |
| Split | The moved observation takes its counter and any correction with it (`carried`) |
| User correction | Offset against the selected base, as for playtime; rebased when the base changes |
| Source stops reporting or never reported | Counter keeps its last value; no reading shows "unknown", not 0 |
| AFK greater than playtime after aggregation | Shown with a notice; neither value is clamped silently |
| User disables collection | New readings rejected; stored rows kept until the user deletes them |

Worked example (one game, threshold 300 s throughout):

1. Playnite on the PC reports `playtime_seconds: 36000, afk_seconds: 3600`. The Deck plugin reports playtime
   `7200` and no AFK field. Playtime is 43 200 s; AFK is 3 600 s **from the PC only**, shown as "AFK 1 h (Deck:
   unknown)". Active time is not derived for the game as a whole, because one source's AFK is unknown.
2. The extension re-sends the same entry with the same `modified_at`: outcome `unchanged`, totals unchanged.
3. The Deck plugin later sends `afk_seconds: 0`: Deck AFK is now a known zero; game AFK stays 3 600 s and active
   time becomes 43 200 - 3 600 = 39 600 s.
4. The user corrects PC AFK to 1 800 s: stored as an offset of -1 800 against 3 600. A new PC reading of 4 000
   gives 2 200 s; the PC's raw values are never edited.
5. The Deck observation is split off to its own game: its counters (playtime 7 200, AFK 0) move with it; the PC
   game keeps 36 000 / 2 200. Merging back restores the step-4 totals without rewriting a reading.

**Display.** "Active time" is a derived line, `playtime - afk_time`, shown only when both are known, the setting is
on and thresholds are known. Users may explicitly select derived active time for statistics; raw playtime remains
the default and is never rewritten. Missing AFK remains unknown, never a zero-duration substitute. Inconsistent
or negative derived values show a notice and are excluded from active-time statistics rather than silently clamped.
This design does not change achievement or notification calculations.

## 7. Privacy, opt-in and retention

* Off by default at three levels: user setting, producer setting, key or capability scope.
* Stored data is a duration (and optional interval boundaries). No input events, window titles or process data.
* User controls: view per-source values, delete one source's AFK counter, delete all AFK data. Included in
  export and backup only when present.
* Retention: cumulative totals live as long as the game (unless explicitly deleted); intervals are pruned after
  90 days through the existing maintenance-job framework. Personal exports include available totals and intervals,
  even after collection is disabled. Disabling ingestion does not hide or delete previously collected data.
* Disabling the setting stops ingestion immediately.

## 8. Alternatives considered

* **Subtract AFK from playtime on ingest:** rejected; not reversible and contradicts "unknown is not zero" and the adjustment model.
* **Free-form JSON metric on observations:** rejected; the closed `GameMetric` enum keeps one policy per metric.
* **Host-side idle detection:** the host cannot see the desktop.
* **Totals only:** smaller storage, but cannot provide the requested per-session history; rejected.

## 9. Phasing

| Phase | Owner | Tasks | Target |
| --- | --- | --- | --- |
| 1. Contract and storage | Host | Playnite and generic contract v2 fields; `GameMetric.AFK_TIME`; counter wiring and AFK adjustments; `game_afk_intervals` migration; `afk_collection_enabled` setting; `library.afk_sync` scope; 90-day pruning job; read API | v0.0.5 |
| 2. Presentation | Host | Separate AFK and derived active-time display; per-source management and deletion; personal export; explicit active-time statistics choice | v0.0.5 or later |
| 3. Plugin boundary | Host | `games.afk.write` capability, AFK fields on `games.import`, [Plugin capabilities](../plugin-capabilities.md) and Plugin API v1 docs | after phase 1 |
| 4. Producers | `obsoletelabs/unnamed_tracking_playnite`, `obsoletelabs/unnamed_tracking_app_plugins` | Opt-in OS idle sampler and v2 negotiation in the extension; any collecting plugin and its examples in the plugin repository | after phases 1 and 3 |

## 10. Test strategy

* Contract: v1 payload unaffected; v2 with the setting off returns `afk: "disabled"` and stores nothing; unknown
  member still 422; `afk_seconds > playtime_seconds` 422; `afk_seconds` without `afk_threshold_seconds` 422;
  threshold outside 60 to 3 600 422; missing `library.afk_sync` scope or `games.afk.write` capability rejected
  while the same request's playtime is still accepted; an `afk_seconds` field in a v1 payload stays 422.
* Aggregation: each row of the section 6 table and each step of the worked example as a `policy.py` or counter
  unit test, with shuffled input order.
* Privacy: disabling stops new ingestion but personal export retains stored AFK; delete-all removes counters,
  members, adjustments and intervals. Pruning at 90 days preserves totals and honours exact retention boundaries.
* Statistics: raw time remains the default; opt-in derived values require known, compatible AFK readings;
  unknown and zero remain distinct; inconsistent values are not silently clamped.
* Migration: upgrade on a populated database, downgrade, single head. Isolation: foreign ids are 404.

## 11. Confirmed decisions and implementation boundary

The [maintainer's issue answers](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/264#issuecomment-6265692706)
approve per-user opt-in, session intervals, derived statistics, thresholds of 60 to 3 600 seconds and a new
capability. Follow-up confirmation on 19 October fixes interval retention at 90 days, retains cumulative totals,
includes AFK in personal exports and makes active-time statistics an explicit choice. No deployment switch is
required. The capability spelling above is proposed for the implementation contract review, not registered today.
Host implementation and companion collection remain separate deliverables; this page completes design decisions only.

## 12. Acceptance-criteria mapping

| Issue criterion | Where |
| --- | --- |
| Producer capabilities today and gaps needing companion/plugin work | Sections 1, 5 |
| Aggregation examples: duplicates, independent sources, merge/split, corrections, missing data | Section 6 and its worked example |
| No raw capture, inferred activity or subtraction by default | Sections 2, 6, 7 |
| Versioned contract, privacy boundaries, tests, phases | Sections 4, 7, 9, 10 |
