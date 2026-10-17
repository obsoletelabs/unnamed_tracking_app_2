# Game name quality, provenance and placeholder repair

Phase 3 of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#98](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/98). It builds on the
`game_field_values` table from the [data model](game-identity-model.md) (#93) and the
[observation registry](game-ingestion.md) (#94). Paths start `src/backend/src/`. No
migration: the schema already holds value, origin, quality, lock, observation and proposal
state.

Code: `features/game_identity/names.py`, `api/routes/game_names.py`; frontend
`components/settings/GameNameReviewSection.vue` (Preferences, Library, Names).

## 1. What is tracked

`games.title` stays the name every screen reads. Beside it, one `current`
`game_field_values` row (`field = 'title'`) records where that name came from
(`origin`: `user`, `source`, `metadata`, `legacy`, `system`), its `quality`
(`placeholder`, `low`, `standard`, `canonical`), the observation that supplied it, and
whether it is locked (`lock_origin`: `legacy` for every existing `locked_fields` entry,
`user` once the user edited it). Earlier names stay as `superseded` rows, a source name
waiting for an answer is `proposed`, and a name the user declined is `rejected`. The lock
is the existing `locked_fields` entry `title`, so the game editor behaves as before. The raw
name each source reported stays on its observation and revisions.

Rows are written lazily and idempotently: every import that records an observation, every
metadata title upgrade and every repair reconciles the row with `games.title`. A name that
changed outside this code is recorded from its current value and lock.

## 2. Recognising placeholders

`classify_title` is the one detector. It matches the **whole** title, never a substring, so
legitimate lookalikes stay `standard`.

| Quality | Examples | Not matched |
| --- | --- | --- |
| `placeholder` | `Steam App 12345`, `steam_app_7`, `AppID 12345`, `Unknown Game`, `Untitled`, `Untitled Game`, `Game 12345` (four or more digits), `App 440` from Steam only, a title equal to the record's numeric external ID | `Untitled Goose Game`, `Unknown 9`, `Game 2`, `Game of Thrones`, `Steamworld Dig`, `AppID Quest`, `12 Minutes`, `App 440` from another provider |
| `low` | empty, only digits or punctuation, `N/A`, `None`, `TBD`, `New Game` | everything else |
| `standard` | any other name a source reported | |
| `canonical` | a standard name supplied by trusted metadata | |

## 3. Precedence (`decide_title`)

| Current name | Offered name | Result |
| --- | --- | --- |
| locked | placeholder or low | kept |
| locked | a different real name | **proposed** for review |
| placeholder or low | a real name (source or metadata) | replaced |
| a real name | placeholder or low | kept |
| a real name, unlocked | an equal or better real name | replaced |
| canonical, unlocked | a standard source name | proposed |
| any | the same name | unchanged |

A name-only change updates the same observation (a new revision) and the same `games` row:
no new source record, game, folder or counter is created and no external ID or Playnite
GUID is touched. Replaying the same source name does not create another proposal, and a
name the user kept is not offered again.

## 4. Where the rules run

- **Library imports** (`get_or_create_game`): Steam, PlayStation, RetroAchievements and
  plugin imports use the in-memory rule, so a later low-quality name never replaces a good
  one and a locked title is untouched. `record_observations` then reconciles the stored
  provenance and opens a proposal for a locked title.
- **Wishlist placeholders**: the wishlist import still adds `Steam app <id>`. A later
  library sync, enrich step or metadata apply that knows the real name upgrades the same
  game, keeping its tracking data.
- **Playnite** and manual entries: names arrive through the same observation hook; the
  Playnite GUID and external ID are never changed.
- **Metadata apply**: records the new title as metadata. New-game enrichment only upgrades
  a recognised placeholder; it never renames a real title.

## 5. Repair of existing games

`POST /api/game-identity/names/repair` (`{"dry_run": true}` previews) records provenance for
every active game and, for each placeholder or low-quality name, uses the newest good name
among its linked observations and their revisions. Unlocked games are renamed; locked games
get a review; games with no known better name are listed as `unresolved` and are repaired
by the next import or metadata refresh. Repeating it changes nothing. It never creates or
deletes a game and never touches IDs, folders, links, counters or tracking data.

## 6. API (scoped to the signed-in user; foreign IDs are 404)

| Route | Purpose |
| --- | --- |
| `GET /api/game-identity/names/reviews` | open name changes: current name, reported name, source |
| `POST .../reviews/{id}/update` | **Update**: take the reported name (a lock stays) |
| `POST .../reviews/{id}/keep` | **Keep current**: remember the answer |
| `POST .../repair` | safe reconciliation described above |
| `GET .../games/{id}` | effective name, quality, origin, lock, raw source names and history |

## 7. Not included

Matching rules that decide identity (#99), the library-wide change notice on the game page
(#101) and backfill beyond the repair call (#102).
