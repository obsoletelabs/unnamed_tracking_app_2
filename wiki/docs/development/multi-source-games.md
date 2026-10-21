# One game from several sources

Gap analysis and first delivery for
[#339](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/339): a game that exists on
Steam, GOG, Epic, Playnite, as a manual entry or in a metadata catalogue such as Atlas should read
as one game, with each source's contribution visible. Backend paths start `src/backend/src/`,
frontend paths `src/frontend/src/`.

## What already exists

| Concern | How it is merged today | Where |
| --- | --- | --- |
| Identity | Merged records form one umbrella (`same_game` edges); a main copy is chosen and can be changed; copies can be split again | `features/game_identity/decisions.py`, `graph.py`, [Game identity UI](game-identity-ui.md) |
| Playtime | Counters per source observation, reconciled (a shared counter once, independent ones added), with priority, grouping and corrections | `features/game_identity/counters.py` |
| Source observations | Playnite and other ingestion paths record what each source reports, with freshness and availability | `features/game_ingestion/`, the **Sources** tab of the identity dialog |
| Saves, screenshots, clips, files | The main copy's page lists items of every copy, labelled with their copy | `features/game_identity/copies.py` (`group=true`) |
| Completion, manual achievements | Overview shows overall values beside each version's own | `features/game_identity/manual.py`, `overview.py` |
| Library layout | `grouped` (one entry per umbrella) or `separate` (every version) | `library_version_display` preference |
| Editable fields | `locked_fields` protect user edits from sync; field history keeps superseded values | `Game.locked_fields`, `GameFieldValue` |

## Gaps found

1. **Achievement list per copy.** The game page listed only the achievements of the page's own
   record, so a Steam and a GOG copy of one game showed two disjoint lists, and an Atlas
   catalogue could not be told apart from a platform sync. *Delivered here.*
2. **Umbrella totals** (`manual.achievements_view`, the Overview) key synced rows by platform id
   only, so a Steam and a GOG achievement with the same name count twice there while the list
   now shows one. *Follow-up.*
3. **Per-field provenance for metadata** (title, description, developer, ...): the app stores the
   winning value and a history, but not "which source said this" in a form the page can show, nor
   a per-field preferred source. *Follow-up; larger design.*
4. **Preferred source for playtime** exists (counter priority) but only in the advanced dialog.
   *Left as is.*
5. **Saves** are listed from all copies but not compared (newest save per copy); sync conflicts
   between copies are not detected. *Left as an issue.*

## Delivered: one achievement list across copies

`GET /api/game/{id}/achievements?group=true` (the game page always sends it) returns the
achievements of every copy the page shows (the same set as the screenshots and files), each
achievement once. `features/achievement_catalog.py::merge_copies`:

- Two entries are the same when they share the platform id **within the same store app** (the
  same Steam app on two records is one achievement, `ACH_1` in two different apps is not), or the
  same name after whitespace and case are normalised (a Steam and a GOG copy). The placeholder
  name of an unrevealed hidden achievement never matches.
- Catalogue entries (metadata providers, see [Metadata providers](metadata-providers.md)) are
  first folded into the synced row with the same platform id.
- An entry is unlocked when any source is, with the earliest unlock time; it stays hidden only
  while every source hides it and none has it unlocked. Missing icons, rarity, points and kind
  are filled from the other sources.
- Every entry carries `sources`: the copy, store, provider, and whether and when that source has
  it unlocked. The page shows the per-source state under the description when more than one
  source reports the achievement, and the provider filter matches any source.
- Entries of the main copy come first, so notes and screenshots linked to an achievement keep
  pointing at the row the page shows. Without `group=true` the endpoint is unchanged apart from
  the added `sources` list.

The **Copies & versions** panel and the identity dialog's **Sources** tab remain the place to see
and change what each copy contributes; no further panel was added.
