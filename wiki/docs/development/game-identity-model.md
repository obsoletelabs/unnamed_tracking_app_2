# Game identity data model

Phase 1 of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#93](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/93). It describes the
**schema that exists now**: thirteen additive tables created by migration `716a1e2c451d`
and the models in `backend/database/models/game_identity.py` and
`game_identity_tracking.py` (paths start `src/backend/src/`). Nothing reads or writes them
yet; consumers move over in #94–#103 in the order given in the
[audit](game-library-identity-audit.md#11-follow-up-order-and-dependencies). Current
behaviour, hazards (`H…`) and prohibitions (`P…`) are cited from that audit.

## 1. Design rules

1. **`games` stays the tracked identity.** A `games` row keeps its ID, folder, notes,
   files and children (P6). `game_identities` only places it in an umbrella and its primary
   key *is* `games.id`. A `games` row without a placement is a standalone legacy identity.
2. **Sources report evidence.** An observation never owns identity and never forces a
   merge. Attaching it to an identity is a separate, movable link with history.
3. **Nothing is destroyed.** Observations, decisions, relationships, links, counter
   memberships and adjustments are archived, ended or superseded, never deleted by
   application code (P1, P2). The only deletes are database cascades from a purged game or
   user.
4. **Decisions outrank automation.** Only `user` (or `migration`, carrying an earlier
   user choice forward) can write `confirmed_same` / `confirmed_separate`; the database
   rejects any other actor. Automatic associations must carry a reason, rule version and
   confidence.
5. **Custom relationships cannot break identity.** Only `same_game` changes how entries are
   merged for display. Every other kind, including user-defined `custom`, only links or
   nests; it never changes membership, counters or aggregation.
6. **Unknown is not zero.** An omitted field is absent from an observation's
   `observed_facts`; a missing item in a partial inventory never sets `unavailable`
   (P3).

## 2. Entities

| Table | Model | Scope and key | Holds |
| --- | --- | --- | --- |
| `game_umbrellas` | `GameUmbrella` | `user_id`; `kind` `game`/`series`/`collection` | The logical game shown once (Minecraft). Series and collections are umbrellas that games join by relationship |
| `game_identities` | `GameIdentity` | PK `game_id` = `games.id` | Placement in one `game` umbrella, `kind` (edition, platform, version, copy, custom), label, edition, `presentation` (`grouped`/`separate`), primary flag, order |
| `game_source_connections` | `GameSourceConnection` | unique `(user_id, source_kind, source, source_scope)` | One account, library or installation. `disconnected_at` records a disconnect; `last_complete_inventory_at` records the last full inventory |
| `game_source_observations` | `GameSourceObservation` | unique `(connection_id, external_id)` | Latest raw facts one source record reported: title, platform, edition, release date, provider IDs, facts, per-field observed times, availability, payload digest, revision |
| `game_source_observation_revisions` | `GameSourceObservationRevision` | unique `(observation_id, revision)` | Append-only changes per revision (only the fields that revision reported) |
| `game_source_observation_links` | `GameSourceObservationLink` | one open link per observation | Which identity an observation is attached to, with `linked_at`/`unlinked_at`, origin and decision |
| `game_identity_decisions` | `GameIdentityDecision` | one current row per subject | Same / separate / unresolved / automatic judgement with actor, reason, rule version, confidence, evidence, prior and resulting state; superseded, never edited |
| `game_relationships` | `GameRelationship` | one active row per `(kind, label, from, to)` | Typed directed edge between identities and/or umbrellas, with `display` and `ended_at` |
| `game_metric_counters` | `GameMetricCounter` | per identity and metric | One counter. Observations in one counter report the same thing |
| `game_metric_counter_members` | `GameMetricCounterMember` | one open membership per observation and metric | Which observations feed a counter, with explicit source `priority` |
| `game_metric_adjustments` | `GameMetricAdjustment` | one current row per counter | User correction as an offset or override, with the base it was made against |
| `game_field_values` | `GameFieldValue` | one `current` row per target and field | Effective field (title, completion, rating) with origin, quality, confidence, observed/edited times, lock and lock origin; `proposed` rows wait for the user |
| `game_manual_achievements` | `GameManualAchievement` | one target: identity or umbrella | User-authored achievements, kept apart from provider rows in `achievements` |

Predefined relationship kinds: `same_game`, `version_of`, `remake_of`, `remaster_of`,
`port_of`, `dlc_of`, `expansion_of`, `standalone_expansion_of`, `mod_of`, `modpack_of`,
`total_conversion_of`, `series_member`, `collection_member`, `related_to`, plus `custom`.
Display behaviours: `default`, `link`, `nest`, `hidden`. The vocabularies live in the model
modules; the database enforces the ones that carry structural rules (see section 4) and the
application validates the open ones, so a new relationship kind needs a code change but no
migration.

## 3. Ownership and tenancy

Every table has `user_id` (`ON DELETE CASCADE` from `users`). Every reference to another
user-owned row is a **composite foreign key that includes `user_id`**, backed by a new
unique `(user_id, id)` key on `games` (`uq_games_user_id_id`) and on each referenced
identity table. The database therefore refuses an identity, link, relationship, decision,
counter, field value or manual achievement that points at another user's game, umbrella,
observation or decision, whatever the application does. Application queries must still
filter `user_id`; cross-user access stays a 404 as in the audit.

Decision pointers (`decision_id` on relationships, links and counter members) use two
foreign keys because PostgreSQL cannot null one column of a composite key: a plain key with
`ON DELETE SET NULL` clears the pointer when a decision is purged with its game, and a
deferred composite key proves the decision belongs to the same user.

Deleting a user removes every row through cascades. Purging a game (the trash sweep)
removes its identity placement, links, counters, field values, manual achievements and any
decision that names it; **observations stay**, detached, so their source identity remains
known. Deleting an umbrella that has identities or a connection that has observations is refused
(`NO ACTION`), and deleting an observation that field values or adjustments still refer to
is refused too, so an application bug cannot orphan history; the application never deletes
observations (an observation's own revisions, links, memberships and decisions would cascade). (`game_links` still lacks
its delete rule; that is audit hazard H3 and unchanged here.)

## 4. Uniqueness, indexes and invariants

Enforced by the database (tests in `tests/test_game_identity_model.py`):

- **Stable source identity:** `(connection_id, external_id)` is unique; the connection is
  unique per `(user, source_kind, source, source_scope)`. The same Steam appid in two
  accounts, or the same Playnite GUID in two libraries, are different records; two users
  never collide. Legacy rows use scope `legacy`; manual entries use source `manual`, scope
  `local` (never guessed, H5/H21).
- **One open link per observation**, one open counter membership per observation and
  metric, one current adjustment per counter, one current decision per subject, one current
  field value per target and field, one open proposal per observation and field, one primary
  identity per umbrella.
- **Relationships:** an active `(kind, label, from, to)` edge is unique (direction
  matters); no self edge; each end is exactly one game or one umbrella; `custom` requires a
  non-blank label and predefined kinds forbid one; `same_game` joins two identities or two
  umbrellas, and an item has at most one active `same_game` target. Ended edges stay, so
  an edge can be recreated and the history keeps both.
- **Decisions:** pair subjects are ordered (`first < second`), so a pair has one key either
  way round; each subject kind uses exactly its own columns; confirmed outcomes need a user
  or migration actor; automatic outcomes need reason, rule version and confidence.
- **Vocabulary and ranges:** closed vocabularies (`source_kind`, availability, decision
  outcome and actor, umbrella kind, field state, adjustment kind, lock origin) are `CHECK`
  constrained; confidences are between 0 and 1; a counter member's metric must equal its
  counter's.
- **Only game umbrellas hold identities:** the identity's reference to its umbrella includes
  the umbrella kind and a check fixes it to `game`.

Cycle detection for relationship types where cycles are meaningless (for example `dlc_of`)
needs graph traversal and is deliberately left to #95; the schema does not forbid
unusual user groupings.

Indexes cover every foreign key and the library read paths: the umbrella list by user and
sort title (active only), identities by umbrella, observations by connection (the unique
key), links and members by observation and by game/counter, and partial indexes for
nullable references and current rows. They are partial so the large majority of rows that
leave a column null cost nothing.

## 5. Archive, disconnect and deletion semantics

| Event | What happens | Never |
| --- | --- | --- |
| Source disconnected | `game_source_connections.disconnected_at` set; credentials are cleared by the existing code as today | Observations, links or counters are removed or marked unavailable |
| Item missing from a partial or failed inventory | Nothing changes | `availability` becomes `unavailable` |
| Complete inventory omits an item, or the source reports `available: false` | `availability = 'unavailable'` and `unavailable_since` set; `last_complete_inventory_at` updated | The observation is deleted |
| Source renames a record | Same observation updated, new revision appended, per-field time updated; a locked title gets a `proposed` field value | A new observation or identity |
| Identical replay | Same `payload_digest`, no write | A new revision |
| Observation moved to another identity | Open link ended (`unlinked_at`), new link opened with the decision that caused it | A change to the external ID |
| Merge | A `same_game` relationship and/or counter membership plus a decision | Deleting or rewriting a `games` row |
| Unmerge | The relationship ended, memberships closed, decision superseded; `prior_state` restores the earlier view | Losing observations or adjustments |
| Umbrella no longer needed | `archived_at` set | Deleting while anything refers to it |
| Game purged from Trash | Cascades remove its placement, links, counters, values, decisions | Removing the observation |

## 6. Counters and metric policies

Identity and counter are separate questions. Two observations can be one identity with
independent counters, or one counter reported by several sources. A counter groups
observations that report the **same** counter; independent counters add up. Per-metric
policies for #96 (the schema records the inputs; reconciliation is not implemented here):

| Metric | Within one counter | Across counters of an identity or umbrella |
| --- | --- | --- |
| `playtime` | Member with the lowest `priority` and the freshest `field_observed_at['playtime_seconds']` wins, then the user adjustment applies | Sum |
| `last_played` | Latest trusted observation | Latest |
| `achievements` | Union by achievement, not by count | Union with per-platform breakdown |
| `completion` | Union of completion facts | Summary ("Completed on 1 of 2 versions") |
| `ownership` | Any | Union |
| `installed` | Per source and platform | Not merged |

Recency is judged **per field** through `field_observed_at`, so a newer name from one
source never makes it win playtime. An adjustment is stored as an offset against a base
observation and its value at that time: a 200 h → 190 h edit stores `-36000` seconds, and
when the base later reports 210 h the effective value is 200 h. If the base counter changes
or is split, `base_observation_id`/`base_value` tell #96 to rebase deliberately.

## 7. Field provenance and manual facts

`game_field_values` records *value, origin, observed time, edited time, quality, confidence
and lock* for one field of one identity or umbrella. Origins: `user`, `source`, `metadata`,
`legacy`, `system`. A value from a source or metadata provider must name its observation; a
`user` value must not. Quality (`placeholder`, `low`, `standard`, `canonical`) lets #98
replace "Steam App 12345" by a trustworthy title without touching a user lock, and the lock
records whether it is a `user` lock, a `legacy` lock (every existing `locked_fields` entry,
H6) or an `api_key` lock.

User-managed completion, ratings and similar facts are field values with origin `user` and
no observation, so they cannot be mistaken for provider observations, and a source sync
(which only writes observations) cannot replace them. Manual achievements are
`game_manual_achievements`, attached to one identity or to an umbrella; provider
achievements stay in `achievements` and `achievement_overrides` is retained (H11).
`match_key` is the user's explicit statement that achievements on different versions are the
same achievement; titles are never an identity.

## 8. Examples

### 8.1 Minecraft Java and Bedrock

The user owns Minecraft on two platforms and wants one library entry.

| Row | Key facts |
| --- | --- |
| `game_umbrellas` | `Minecraft` (`kind=game`) |
| `games` | "Minecraft Java Edition" (existing row, platform PC) and "Minecraft Bedrock Edition" (existing row) |
| `game_identities` | Java: `kind=edition`, `label=Java Edition`, `is_primary`, `presentation=grouped`. Bedrock: `kind=edition`, `label=Bedrock Edition`, `presentation=grouped` |
| `game_relationships` | none needed: sharing the umbrella is the grouping. Optional `version_of` Bedrock → Java to record the semantic relationship |

Opening Minecraft shows aggregate values over both identities; entering Bedrock shows its
own. Setting Bedrock's `presentation` to `separate` shows it as its own top-level entry
while it stays in the umbrella and keeps its relationship, with no data change. A mobile
copy is a third identity (`kind=platform`, label "Mobile"). Minecraft Dungeons is a
different game: its own umbrella, plus an optional `custom` or `related_to` edge, which
cannot alter Minecraft's identities or counters (rule 5).

### 8.2 Playnite plus a manual record: the same counter

Playnite reports Minecraft with 200 h; the user also tracked it by hand at 195 h.

| Row | Key facts |
| --- | --- |
| `game_source_connections` | `host/playnite/legacy` (or the Playnite library ID once #100 supplies one) and `manual/manual/local` |
| `game_source_observations` | `(playnite, guid)` with `observed_facts.playtime_seconds = 720000`; `(manual, entry)` with `702000` |
| `game_source_observation_links` | both observations open-linked to the same `games` row |
| `game_metric_counters` | one `playtime` counter for the identity |
| `game_metric_counter_members` | both observations in that counter, manual `priority 0` (deliberately edited more recently), Playnite `1` |

The effective playtime is 195 h, never 395 h. Adding the user's Steam copy later creates a
**second** counter on its own identity (Steam observation, 100 h); the umbrella total is the
sum of independent counters. Splitting the merge closes the manual observation's
membership; both observations, their revisions and any adjustment remain, and the
effective views recover from them.

### 8.3 Existing rows (no migration yet)

A legacy Steam row maps to a `steam/legacy` connection, an observation keyed by its
`external_id`, an open link to its own `games.id`, a one-member counter per metric and an
umbrella of its own. An `owned_copy` group maps to one umbrella with the main as primary,
each copy a `copy` identity and a `same_game` decision (`migration` actor); a dismissal in
`game_duplicate_dismissals` maps to a `confirmed_separate` decision. This mapping is #102's
work; #93 only guarantees the schema can hold it without changing any `games.id`.

## 9. Compatibility and migration

- `716a1e2c451d` follows `c10098e58426`, the only head. It creates tables and one unique
  key, uses the create-if-missing helpers so database adoption can replay it, and touches
  no existing row. `games`, `achievements`, `game_duplicate_dismissals`, `parent_game_id`
  and `relationship_type` keep their behaviour; every Playnite, Steam and plugin path is
  unchanged.
- **Autogenerate.** On the audited baseline it proposed dropping the seven retained legacy
  tables and twelve indexes (H22); `migrations/env.py` already filters them. The reviewed
  migration contains no drop, `alter_column` or rename, and
  `tests/test_game_identity_migration_upgrade.py` asserts that autogenerate against the
  migrated database proposes nothing.
- **Downgrade** is allowed while the new tables are empty and refuses, naming the tables,
  once they hold data, because decisions and manual tracking exist nowhere else. Take a
  database dump before upgrading ([P9](game-library-identity-audit.md#1-rollout-prohibitions)).
- The new models are not imported by application code yet; `migrations/env.py` registers
  them for autogenerate. Consumers import them when they start using them.

## 10. Hand-off to later phases

| Issue | Uses |
| --- | --- |
| #94 ingestion | connections, observations, revisions, links; `payload_digest`; `availability` rules; legacy scopes |
| #95 decisions | `game_identity_decisions`, `same_game` relationships, link moves, cycle checks |
| #96 reconciliation | counters, members, adjustments, `field_observed_at` |
| #97 manual tracking | manual achievements, user field values |
| #98 field quality | field values, proposals, quality and lock origin |
| #99 matching | decisions with evidence, rule version and confidence |
| #100 Playnite | `playnite` connections and observations keyed by GUID |
| #101 UI | umbrellas, identities, `presentation`, `display` |
| #102 migration | the mapping in section 8.3, run by the [resumable backfill](game-identity-migration.md); progress in `game_identity_backfills` |
