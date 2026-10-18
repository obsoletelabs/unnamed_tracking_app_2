# Design: optional AFK-time observations

Status: **proposal for review** (task [#264](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/264), part of
[#248](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/248)). Nothing here is implemented or enabled.
It builds on the [game identity model](../game-identity-model.md), [counters](../game-identity-counters.md) and
[ingestion](../game-ingestion.md).

## 1. What is being asked

A game that is open while nobody plays it inflates playtime. Some people want to *see* how much of a session was idle
("AFK time") so they can judge real play. The upstream request names no producer, no idle threshold and no relationship to
playtime.

**Non-goals:** capturing keyboard or mouse input; inferring activity from the screen or processes; subtracting AFK from
playtime automatically; any default-on collection; a server-side idle detector (the server cannot know).

## 2. What exists today

| Capability | Where | Relevance |
| --- | --- | --- |
| Versioned source observations | `features/game_ingestion/contract.py`, `ObservedFacts` (`extra="forbid"`), contract version 1 | the public, additive way for a producer to report a fact |
| Independent counters per metric | `game_identity/counters.py`, `GameMetric` = playtime, last_played, achievements, completion, ownership, installed | decides which observations report the *same counter* and how an effective value is derived |
| User adjustments | `GameMetricAdjustment` (offset or override), `policy.apply_playtime_adjustment` | the existing, explicit way to correct a number |
| Freshness and source priority | `policy.pick_preferred`, `Reading.observed_at`, `field_observed_at` | how conflicting readings from one counter are resolved |
| Producers | Playnite extension (`obsoletelabs/unnamed_tracking_playnite`), plugins through the game-import capability, manual entry | the only parties that could measure idleness |

**Gap:** no producer reports idle time, the contract has no field for it, and `GameMetric` is a closed enum validated in
code. All collection belongs in a companion repository; the host owns only a contract, storage and presentation.

## 3. Proposed metric: `afk`

A new `GameMetric.AFK` ("away time"), reported as `afk_seconds` in `ObservedFacts`:

```python
afk_seconds: int | None = Field(default=None, ge=0, le=2_000_000_000)
```

* **Unit:** whole seconds, cumulative for the observation's lifetime, exactly like `playtime_seconds`.
* **Unknown is not zero.** An omitted field means the producer does not measure it (the existing contract rule). `0`
  means "measured, and no idle time". The UI must never show "0 AFK" for a source that does not report it.
* **Bounded by playtime of the same observation.** A producer reporting `afk_seconds > playtime_seconds` for the same
  observation is rejected with a clear error (it is a bug in the producer, not data).
* **No effect on `playtime`.** The two metrics are independent counters.

### 3.1 Why a separate metric, not a playtime adjustment

Subtracting idle time from stored playtime would silently rewrite a number other features (statuses, stats, achievements
progression, notifications) already depend on, and it would be wrong whenever a producer's idle detection is. Showing AFK
*next to* playtime keeps every existing meaning intact and lets a person decide.

### 3.2 Presentation

* Game page, Copies & versions panel: "Played 12 h · away 2 h 10 min (reported by Playnite)", only when at least one
  source reported `afk_seconds`.
* Never combined into a single "active playtime" number by default. A **user-chosen, per-game** switch may later show
  "playtime excluding away time" (= playtime − effective AFK, floored at 0) clearly labelled as derived; it never writes to
  playtime.

## 4. Aggregation

AFK follows the same counter rules as playtime (independent counters add; observations of the same counter are never
summed):

| Situation | Rule |
| --- | --- |
| One source, repeated reports | the freshest reading of that counter wins; a *lower* later value is accepted (cumulative counters can be corrected) but flagged `decreased` in the explanation |
| Same session reported by two sources (Playnite and a desktop app) | the user groups them as one counter (existing "same counter" grouping); the preferred source wins, the other is shown as a loser with its value |
| Two independent sources (a PC and a handheld) | separate counters; effective AFK is their **sum**, as playtime |
| Merge of two games | counters survive; sums follow the merged group |
| Split | counters move with their observations; adjustments carry as for playtime |
| Missing data from one of several sources | the effective value covers only sources that reported; the UI says "from N of M sources" |
| User correction | the existing adjustment mechanism (offset or override) applies to the AFK counter; the original reading is kept and shown |
| Overlapping sessions across sources | not de-duplicated automatically; grouping is the user's explicit decision (same as playtime) |

### Worked examples

1. *Duplicate observation.* Playnite sends `playtime=7200, afk=900` twice. One reading, one counter, effective AFK 900.
2. *Independent sources.* PC counter 900 s + handheld counter 300 s → effective 1 200 s ("away 20 min, from 2 of 2 sources").
3. *Correction.* Producer first reports 1 800 s, later 1 500 s after fixing its idle threshold → effective 1 500 s, explain
   shows "lowered from 1 800".
4. *Missing.* Steam reports playtime only; Playnite reports AFK → effective AFK from "1 of 2 sources".
5. *Merge.* Two Playnite copies merged → their AFK counters stay separate counters and add.
6. *Override.* The user sets AFK to 0 for one game with a note → shown as "set by you".

## 5. Collection, consent and privacy

* **Opt-in at the producer and at the host.** The Playnite extension (or a plugin) ships with AFK reporting **off**. The
  host additionally ignores `afk_seconds` unless the user enabled *Accept away-time reports* in their preferences
  (default off); an unaccepted report is dropped, counted, and never stored.
* **What may be sent:** one integer per game. No timestamps of idle periods, no input events, no window titles, no
  process lists. A producer that sends more is rejected by the contract (`extra="forbid"`).
* **How a producer decides what idle means** is its own, documented, configurable choice (for example "no input for
  5 minutes while the game window is focused"). The host does not define a threshold and does not claim accuracy.
* Deleting a game or account deletes its AFK counters (existing cascade). Export includes AFK counters like other
  counters; import restores them without re-enabling collection.

## 6. Versioned contract

`ObservedFacts` is additive: adding an optional field does **not** require a new contract version, because clients ignoring
it are unaffected and the omitted-field rule keeps old producers valid. The Playnite contract
(`GET /api/playnite/contract`) lists `features: ["afk_seconds"]` only once the host accepts it, so a producer can negotiate.
A producer sending `afk_seconds` to a host without the feature gets the existing `extra_forbidden` 422, which it must treat
as "not supported".

## 7. Test plan

* Contract: accepts valid, rejects negative, > bound, wrong type, and `afk > playtime`; unknown fields still rejected.
* Opt-in: report dropped and counted when the preference is off; stored when on; turning it off stops new storage but keeps
  history.
* Counters: the six examples above, plus merge/split round trip, adjustment carry, restart persistence, `explain` output.
* UI: unknown vs zero, partial-source wording, never merged into playtime by default.
* Privacy: no endpoint returns raw readings to other users; export/import round trip.
* Property test: effective AFK never exceeds effective playtime of the same counters without an explicit user override.

## 8. Phases

1. **Host contract (no UI):** `GameMetric.AFK`, `afk_seconds`, the bound check, opt-in preference, tests. Reviewable alone;
   changes nothing until a producer sends it.
2. **Presentation:** the Copies & versions line and explanation; the derived "excluding away time" per-game switch.
3. **Playnite extension (companion repository):** optional, off by default, configurable idle definition, reports one integer
   per game. Out of scope for the host repository.
4. **Plugin capability docs:** how a plugin producer reports it through the game-import capability.

## 9. Open questions

1. Is "away time" the right user-facing name? *Recommendation: yes; "AFK" is jargon.*
2. Allow the host to ever auto-subtract? *Recommendation: no; only a user-chosen derived display.*
3. Should AFK appear in statistics? *Recommendation: not until the derived switch exists and has been used.*
