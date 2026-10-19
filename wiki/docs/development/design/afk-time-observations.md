# Optional AFK-time observations and aggregation semantics

Design proposal for [#264](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/264)
(source: Rosefall #65). **Design only.** Items marked **OPEN QUESTION** need a maintainer decision. Paths start
`src/backend/src/`.

## 1. Problem and current state

Users want to know how much recorded playtime was spent away from the keyboard. Nothing in the host defines a
producer, an idle threshold, or how such a value relates to playtime.

What exists today:

* Observations and counters ([Game identity counters](../game-identity-counters.md),
  [Playnite observations](../playnite-observations.md)). `database/models/game_identity_tracking.py` defines
  `GameMetric` (`playtime`, `last_played`, `achievements`, `completion`, `ownership`, `installed`),
  `game_metric_counters`, `game_metric_counter_members` and `game_metric_adjustments`. Rules: unknown is not zero;
  one counter is one reading and never a sum; independent counters combine by a per-metric rule in
  `features/game_identity/policy.py`; user corrections are offsets and never edit a source value.
* Producers: the Playnite contract v1 (`POST /api/playnite/observations`) accepts a closed list of fields and
  rejects unknown members with 422, so an extension cannot send AFK data today. Plugins import through
  `plugin_api/game_import.py` and `plugin_api/game_library.py`, which carry tags, playtime and similar facts but
  no AFK field.
* No code in the repository measures idleness.

## 2. Goals and non-goals

Goals: an **optional, off by default** metric `afk_time` in seconds, ingested through the same observation and
counter machinery, shown separately from playtime, with explicit and reversible user control.

Non-goals (enforced, not only documented): raw keyboard or mouse capture; per-key, per-window or screenshot data;
inferred activity from other signals; **subtracting AFK from stored playtime** or changing how `playtime` is
selected; a host-side idle detector. Collection belongs in the companion and plugin repositories.

## 3. Proposed data model (additive)

Prefer **no new table**: extend the closed enum and reuse the counter tables.

* Add `AFK_TIME = "afk_time"` to `GameMetric` (a Python `StrEnum`, validated in code per its docstring). Check
  whether the counter's metric column is a native database enum or a string: if native, one additive
  `ADD VALUE` migration; otherwise none.
* Observation facts carry `afk_seconds` (integer >= 0) and `afk_threshold_seconds` (the producer's idle
  definition, e.g. 300) next to `playtime_seconds`. A reading with a different threshold is a different counter
  and is never grouped automatically.
* Only if per-session data is accepted (**OPEN QUESTION 2**): `game_afk_intervals(observation_id, started_at,
  ended_at)`, capped at 1 000 rows per observation, used only to de-duplicate overlap. Totals remain the displayed value.
* Consent: a per-user setting `afk_collection_enabled` (default false) in the existing preferences store, and a
  scope on the API key or plugin grant so a key without it cannot submit the field.

## 4. Versioned contract

`contract_version` is raised additively; v1 stays valid and unchanged. A v2 entry may include `afk_seconds`,
`afk_threshold_seconds` and optional `period_start` / `period_end` (UTC). Rules:

* Omitted means unknown, never zero. `0` is a real "present the whole time".
* `afk_seconds` greater than the same entry's `playtime_seconds` is 422. It is not otherwise validated against stored playtime.
* Unless the user setting is on, the host does not store the field and answers with an explicit
  `afk: "disabled"` outcome so the producer can show why.
* Plugins need a capability such as `game_observations.afk_time:write`, following
  [Plugin permissions](../plugin-permissions.md) (declared, user-granted, gateway-enforced). Without it the field is
  rejected. The capability is documented in [Plugin capabilities](../plugin-capabilities.md) when implemented.

## 5. Producer gaps

| Producer | Today | Gap |
| --- | --- | --- |
| Playnite extension (`obsoletelabs/unnamed_tracking_playnite`) | Reports playtime, last played, installed | Opt-in idle sampler (OS idle time only, no input content) and contract v2 support |
| Desktop companion | None | Separate product and repository, same contract |
| Plugin | Can import playtime | New capability; plugin-repository work, not host |
| Host | n/a | Owns only the contract, storage, consent and enforcement |

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

**Display.** "Active time" is a derived line, `playtime - afk_time`, shown only when both are known, the setting is
on and thresholds are known. It is never written back, never replaces `playtime`, and does not feed statistics,
achievements or notifications (**OPEN QUESTION 3**).

## 7. Privacy, opt-in and retention

* Off by default at three levels: user setting, producer setting, key or capability scope.
* Stored data is a duration (and optional interval boundaries). No input events, window titles or process data.
* User controls: view per-source values, delete one source's AFK counter, delete all AFK data. Included in
  export and backup only when present.
* Retention: values live as long as the game; optional intervals are pruned after 90 days (**OPEN QUESTION 4**).
* Disabling the setting stops ingestion immediately.

## 8. Alternatives considered

* **Subtract AFK from playtime on ingest:** rejected; not reversible and contradicts "unknown is not zero" and the adjustment model.
* **Free-form JSON metric on observations:** rejected; the closed `GameMetric` enum keeps one policy per metric.
* **Host-side idle detection:** the host cannot see the desktop.
* **Interval table first:** more data than the issue needs; totals suffice for phase 1.

## 9. Phasing

1. Host contract v2 fields, consent setting, `GameMetric.AFK_TIME`, policy and metrics API (no UI).
2. Read-only display and per-source management in the game metrics view.
3. Plugin capability and documentation for third-party producers.
4. Producer implementations in their own repositories (outside this host change).

## 10. Test strategy

* Contract: v1 payload unaffected; v2 with the setting off returns `afk: "disabled"` and stores nothing; unknown
  member still 422; `afk_seconds > playtime_seconds` 422; missing capability rejected.
* Aggregation: each row of the section 6 table as a `policy.py` unit test, with shuffled input order.
* Privacy: export has no AFK data when disabled; delete-all removes counters, members and adjustments.
* Migration: upgrade on a populated database, downgrade, single head. Isolation: foreign ids are 404.

## 11. OPEN QUESTIONS for the maintainer

1. Should the AFK metric sit behind a per-user setting only (proposed), or also an admin deployment switch?
2. Are per-session intervals wanted, or are totals enough? (Intervals add a table and a retention job.)
3. May the UI show a derived "active time", and may it ever feed statistics? Proposed: display only.
4. Retention for intervals (90 days proposed), and is AFK data part of exports by default?
5. Which idle thresholds should the contract accept (proposed 60 s to 3 600 s)?
6. Is a new plugin capability name acceptable, or should AFK ride on an existing game-observation capability?

## 12. Acceptance-criteria mapping

| Issue criterion | Where |
| --- | --- |
| Producer capabilities today and gaps needing companion/plugin work | Sections 1, 5 |
| Aggregation examples: duplicates, independent sources, merge/split, corrections, missing data | Section 6 |
| No raw capture, inferred activity or subtraction by default | Sections 2, 6, 7 |
| Versioned contract, privacy boundaries, tests, phases | Sections 4, 7, 9, 10 |
