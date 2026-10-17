# Playnite observations and compatibility

Phase 4 of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#100](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/100). It builds on
[ingestion](game-ingestion.md), [decisions](game-identity-decisions.md) and
[counters](game-identity-counters.md). Paths start `src/backend/src/`. No migration. The extension
side lives in [`obsoletelabs/unnamed_tracking_playnite`](https://github.com/obsoletelabs/unnamed_tracking_playnite)
(see its `api-contract.md`).

## 1. What was audited

The extension source was read against the host (extension 0.2.0, `main` at `8e5763e`).

| Area | What the extension does | Host consequence |
| --- | --- | --- |
| Identity | `Game.Id` (Playnite GUID) is sent as `playnite_guid`; the folder `playnite-<name>-<guid>` is kept stable across renames; lookup pages `GET /api/game/list` | The GUID is the stable source ID of the observation (`external_id`). A rename never creates a game. |
| Auth | `Authorization: Bearer utk_...` on every request, key redacted from logs and error bodies | Unchanged; the new routes use the same `get_current_user`. |
| Sync triggers | Manual library sync, startup sync, selected game, game-stopped update (linked games only), save sync | Only a whole-library read may assert an inventory. |
| Fields | title, sort title, description, release date, developer/publisher/series, tags, features, collections, links, source, age rating, favourite, notes, playtime, rating, completion status, artwork; platform only as `Platform:` tags | Platform, last played and installed state had no route. They now travel as observation facts. |
| Errors | Per-game failures with HTTP status and body; 409 duplicate folder, 409 Trash; artwork failures are warnings | Unchanged. See "Locked names" below for the 403 that was fixed. |
| Gaps found | Every update re-sent the title and status; a title the user had locked made the **whole** update 403; Playnite's own changes were stored as user locks; no last played, platform or installed state; no complete-inventory signal | Fixed here (sections 3 and 4). |

Playnite has no ownership or achievement model of its own. The extension reports `owned: false` only
for a Wishlist entry, never guesses ownership, and sends no achievement counts. The contract accepts
`achievements_total` / `achievements_unlocked` for a future extension version.

## 2. Contract v1 (`features/game_ingestion/playnite.py`, `api/routes/playnite.py`)

All routes use the same authentication as the game routes. Nothing here creates, merges or splits a
canonical identity.

| Route | Purpose |
| --- | --- |
| `GET /api/playnite/contract` | Version negotiation and an authenticated connection test: `supported_versions`, `current_version`, `features`, `limits`, the legacy routes still served. |
| `POST /api/playnite/observations` | `{contract_version, library_id?, games: [...]}` with 1 to 500 distinct entries. |
| `POST /api/playnite/inventory` | `{contract_version, library_id?, playnite_ids: [...]}` (up to 50 000): the complete list of IDs the library holds. |

An entry carries `playnite_id` (the GUID) and any of `name`, `platforms`, `release_date`,
`playtime_seconds`, `last_played_at`, `installed`, `owned`, `favorite`, `status`, `achievements_total`,
`achievements_unlocked`, `source`, `developer`, `publisher`, `series`, `modified_at`. Omitted means
unknown: it never clears a stored value. Unknown members are rejected (422), so a newer payload cannot
be half understood.

* **Version negotiation.** An unsupported `contract_version` is HTTP 400 `unsupported_contract_version`
  with the supported list, before anything is read or written. A deployed extension that never calls
  these routes keeps using the original ones.
* **Linking.** An entry is attached only to the active game that already carries its `playnite_guid`.
  An unknown ID is reported `unlinked` (the extension creates it through `POST /api/game/create`, which
  records the same observation); an ID whose game is in Trash is `in_trash` and is not recreated.
* **Same record, two paths.** The default connection is host `playnite` / scope `legacy`, the one the
  original routes use, so an upgraded extension continues the same observations. `library_id` selects
  scope `library:<id>` for a second installation; it attaches to the same game.
* **Idempotent and ordered.** Replaying a payload is `unchanged`; `modified_at` becomes
  `source_updated_at`, so a late retry of an older report is `stale` and cannot rewind the observation.
  `last_played_at` only moves a game's last played forward.
* **Reported results.** `{playnite_id, game_id, outcome}` per entry: `created`, `updated`, `unchanged`,
  `stale`, `unlinked`, `in_trash`.

## 3. Partial inventories and renames

* A batch, a failed page, a cancelled sync or a selected-game sync changes no availability. Only
  `POST /api/playnite/inventory` (sent by the extension after it read and reported the **whole** library,
  including games carrying the ignore tag) marks an absent observation `unavailable`. Rows, links and
  history are never deleted; reporting the entry again makes it `available`. An inventory for a library
  that never reported anything is a no-op.
* A new name for a known GUID updates the observation (a revision) and, through the original update
  route, the game's title unless the user locked it.

## 4. Protection of the user's data (original update route)

For an API-key `PATCH /api/game/update/{id}` on a game with a `playnite_guid` (`playnite.protect_manual_edits`):

* **Locked names.** A title the user locked is kept and the rest of the update (playtime, favourite, ...)
  still applies, instead of failing the whole update with 403. Playnite's name is still recorded as its
  observation.
* **Locked status.** A status the user set by hand (`status` in `locked_fields`, see
  manual completion, #97) is kept; `completion_date`
  with it. Playnite's reported status stays on the observation.
* **No self-locking.** A change Playnite makes no longer joins `locked_fields` (it used to, so a second
  rename from Playnite hit the 403 above and metadata refreshes treated Playnite edits as the user's).
* Counters and persistent corrections are untouched by a sync: the user's playtime correction rides on
  the next Playnite reading (#96).

## 5. Compatibility

`/api/game/list`, `/api/game/create`, `/api/game/update/{id}`, assets and archives keep their paths,
payloads and status codes (`tests/test_playnite_contract.py` is unchanged). The extension probes
`GET /api/playnite/contract` and falls back to those routes alone when the host answers 404, something
unreadable or a version it does not speak. A host that receives an extension speaking a newer contract
answers 400 and stores nothing.

## 6. Tests

`tests/test_playnite_observations.py` runs over HTTP with a real `utk_` key: negotiation, refused
versions, malformed batches, a representative entry (platform, last played, installed, owned,
achievements) and its idempotent replay, stale reports, unknown / Trash / other-user entries, library
scopes, renames (no lock, no duplicate), locked name and status, partial pages, complete inventory and
restoration, and the full flow: Playnite import, merge with a manual record, sync, manual playtime
correction and locks, sync again, unmerge.
