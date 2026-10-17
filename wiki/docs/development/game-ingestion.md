# Game ingestion

Phase 1 of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#94](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/94). It describes the
**code that exists now**: a versioned, source-agnostic observation contract and an
idempotent registry on top of the [identity data model](game-identity-model.md). Paths start
`src/backend/src/`. No migration: the tables were created by `716a1e2c451d` (#93).

## 1. Principles

1. **Evidence first.** A source reports what it knows as an *observation*; it is stored
   with its provenance (connection, stable external ID, per-field observed times, revision
   history) before anything decides what game it is.
2. **Sources never decide identity.** Ingestion creates no umbrella, identity, decision or
   relationship, and never merges or renames a `games` row. Attaching an observation to a
   `games` row is a recorded link.
3. **Unknown is not zero.** An omitted (or `null`) field is unknown and never clears,
   resets or deletes a stored value.
4. **Absence is not removal.** Only a complete inventory or an explicit `available: false`
   can make an observation unavailable. Partial, failed or cancelled pulls change nothing.
5. **Nothing is destroyed.** Observations, revisions and links are never deleted by
   application code; disconnecting a source only records `disconnected_at`.

## 2. Contract v1

`features/game_ingestion/contract.py`.

| Model | Purpose |
| --- | --- |
| `SourceConnectionRef` | `source_kind` (`host`, `plugin`, `manual`, `metadata`), `source` (host name, plugin ID, `manual`, provider ID), `source_scope` (account, library, `legacy` or `local`), optional `label` |
| `SourceObservationV1` | `external_id` (the stable source ID) plus any of: `title`, `platform`, `edition`, `release_date`, `provider_ids`, `facts`, `source_updated_at`, `available` |
| `ObservedFacts` | `playtime_seconds`, `last_played_at`, `owned`, `wishlisted`, `installed`, `status`, `favorite`, `achievements_total`, `achievements_unlocked`, `description`, `developer`, `publisher`, `series`, `tags`, `features` |
| `ObservationBatchV1` | `contract_version` (only `1` is accepted), one connection, 1 to 5000 observations with distinct `external_id`s |

The models reject unknown fields, blank IDs and out-of-range values. A later contract
version is a new model; version 1 keeps its meaning.

## 3. The registry

`features/game_ingestion/service.py`; rows live in `game_source_connections`,
`game_source_observations`, `game_source_observation_revisions` and
`game_source_observation_links`.

| Operation | Behaviour |
| --- | --- |
| `ingest_batch` | Upserts the connection by `(user, source_kind, source, source_scope)` and each observation by `(connection, external_id)`. One insert-if-absent statement plus row locks make concurrent identical deliveries produce one observation. |
| Identical replay | No diff against the stored state: no write, no revision, `outcome = unchanged`. |
| Changed payload | Only reported fields that differ are written (`provider_ids` and `facts` are merged key by key). One revision is appended holding just those changes; every field in the payload gets a fresh `field_observed_at`. |
| Rename | Same observation, new `observed_title` and revision. |
| Late retry | A payload whose `source_updated_at` is older than the stored one is ignored (`stale`). |
| `complete_inventory` | The only place an absent record becomes `unavailable`; sets `last_complete_inventory_at`. Ignored for a disconnected connection. |
| `disconnect_connection` | Sets `disconnected_at`. Observations, links and history are untouched. |
| Reconnect | The next delivery for a disconnected connection clears `disconnected_at` and reuses the same connection and observation rows. |
| `relink_observation` | Moves a record to another `games` row: ends the open link and opens a new one. Requires an origin of `user`, `migration` or `automatic`; ingestion is not accepted. The observation and its external ID do not change. |

Tenancy: every query filters `user_id`; the database composite keys reject cross-user
references. The same external ID under another scope or user is a different record.

### Attaching to a game

Ingestion accepts an optional `attach` map (external ID to the `games` row a caller already
resolved). A record with **no link history** is linked (`origin = ingest`). A record that
was ever linked keeps its state: a reviewed move or an unlink is never undone by a sync.
The one exception is a link whose game is in Trash while the caller resolved a different
active game: the old link is ended (kept as history) and a new one opened.

## 4. Compatibility adapter

`features/game_ingestion/legacy.py`. Existing routes keep their paths, payloads, status
codes and matching rules. After a route resolves its `games` row exactly as before, it
records what the source reported. Fields the source did not report are not sent, and a
malformed optional value is dropped rather than failing the import.

| Path | Connection | External ID | Recorded |
| --- | --- | --- | --- |
| Steam library sync, achievements step, wishlist | host `steam` / `legacy` | app ID | title, owned, playtime, last played, achievement counts when fetched; wishlist records only the ID and `wishlisted` (the `Steam app N` placeholder is not a source name). Steam omission never marks anything unavailable (#72). |
| RetroAchievements, PlayStation sync | host `retroachievements` / `playstation`, `legacy` | game ID / `npCommunicationId` | as above; a completed pull calls `complete_inventory` |
| Playnite `POST /api/game/create`, API-key `PATCH /api/game/update/{id}` | host `playnite` / `legacy` | `playnite_guid` | only the fields the request carried |
| Manual create and edits of a manually created game | manual `manual` / `local` | the game's own ID | the fields the request carried |
| Plugin `games.import` | plugin ID / the batch `source_scope` | plugin `external_id` | title, provider IDs, playtime, descriptive fields, `available` |
| Steam and PlayStation disconnect | as above | | `disconnected_at` only |

Edits a user makes to an imported game are tracking choices, not source evidence, and are
not recorded as observations.

**Trash policy (audit H18).** Plugin imports already report a game in Trash as a conflict.
Playnite now does the same: creating a GUID whose game is in Trash returns HTTP 409
(`playnite_game_in_trash`) instead of a second row. Host library syncs (Steam,
RetroAchievements, PlayStation) keep re-adding a trashed record as before; the observation
is not duplicated and its link follows the new active game.

## 5. Not included

The public HTTP ingestion endpoint and the Playnite library scope belong to #100; identity
decisions to #95; counters and reconciliation to #96; the existing-library backfill, which
links `legacy` observations for rows imported before this change, to #102. Rows imported
before this change get their observation the next time their source syncs.
