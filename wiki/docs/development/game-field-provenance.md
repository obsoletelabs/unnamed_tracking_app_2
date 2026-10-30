# Per-field provenance and a preferred metadata source (design)

Design for [#348](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/348) and the
metadata half of [#349](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/349).
**Steps 1 to 3 (provenance rows, preferences, page UI) are implemented.** Paths start `src/backend/src/`.

## What exists today

- `games` columns hold the value every consumer reads. A metadata refresh
  (`api/routes/game_metadata.py`) fills a field unless it is in `Game.locked_fields`; locked
  fields are reported as `skipped_locked_fields`.
- `game_field_changes` (`GameFieldChange`) records old and new text per changed field, written by
  `_record_field_changes`. It does not say which source supplied the new value.
- `game_field_values` (`GameFieldValue`) records provenance (`origin`, `observation_id`, quality,
  lock) but is only maintained for `title` (`features/game_identity/names.py`) and the tracking
  facts (completion status, counters).
- A merged game shows the main copy's metadata. The playtime source is already selectable
  (counter priority, #391); there is no equivalent for metadata.

So the gap is not storage for one value but (a) knowing where each other field's value came
from, and (b) letting a person say "for this field, trust that source".

## Decisions

### 1. Record provenance in `game_field_values`, not a new table

`GameFieldValue` already has the shape (`field`, `value`, `state`, `origin`, `observation_id`,
`observed_at`, `edited_at`, lock) and a unique `current` row per game and field. Extend its use
from `title` to the displayable metadata fields rather than adding a parallel table. Origins stay
the existing closed set (`user`, `source`, `metadata`, `legacy`, `system`).

A metadata refresh has no source observation, and the table requires one for `source` and
`metadata` origins. So a nullable `source_label` column names the provider, and the check
constraint now allows a `metadata` value that names an observation **or** a provider (a `user`
value still names neither). A refresh writes `origin = metadata` with the provider; a manual edit
writes `origin = user`.

Fields in scope: `description`, `developer`, `publisher`, `series`, `tags`, `features`,
`age_rating`, `release_date`. Excluded: `title` (it keeps its own rules in
`features/game_identity/names.py`), ids, `provider_ids`, status and pricing, and artwork.

Existing games have no rows. Backfill lazily: the first time a field is read for provenance, a
missing row is shown as "unknown source" (origin `legacy`) rather than guessed, and the next
refresh writes a real row. No data migration is needed beyond what the table already has.

### 2. A preference is a per-game, per-field source choice

Add one small table, `game_field_preferences(user_id, game_id, field, source_key)`, unique on
`(game_id, field)`. `source_key` names a contributor: a linked copy (`copy:<game_id>`) or a
metadata provider (`provider:<id>`). It is deliberately separate from `locked_fields`:

- a **lock** says "do not change this value" and stays authoritative;
- a **preference** says "when sources disagree, take this one" and is applied on refresh.

Precedence on refresh: lock > preference > existing quality ranking (`names.decide_title`
generalised) > most recent. A preferred source that has no value for a field does not blank it;
the next source in the ranking fills it. Preferences survive refreshes because they are only read.

Why not a per-umbrella preference: umbrellas change as copies merge and split, and the main copy
can change. A per-game preference moves with the copy it names, and the umbrella page resolves
each field by walking its copies.

### 3. Presentation: compact, progressive

On the game page, a field's source appears as a small label beside the value only when more than
one source disagrees. Selecting it opens a list of the values each source reports with the
current one marked and a "Prefer this source" action. The existing Sources summary (#391) gains a
"Metadata" row showing the most-used preferred source, linking to the same list. Nothing is added
for games with a single source.

## Out of scope

- Merging values (for example union of genres from several sources) beyond what refresh already
  does.
- Provenance for ids, saves and files.
- Changing lock semantics.

## Delivery plan

1. **Implemented.** Provenance rows are written for the in-scope fields on a metadata refresh
   and on a manual edit (`features/game_identity/field_provenance.py`, migration
   `e5a1c7d93b46`). `GET /api/game/{id}/field-sources` returns, per recorded field, the origin,
   provider, time and lock. Sync paths (Playnite, store imports) do not write rows yet.
2. **Implemented.** `game_field_preferences` (migration `b7d2e4f19a38`,
   `features/game_identity/field_preferences.py`). `PUT` and `DELETE`
   `/api/game/{id}/field-preferences/{field}` set and clear a preference (owner only; the field
   must be in `PROVENANCE_FIELDS`; the source is `provider:<id>` known from a recorded refresh, or
   `copy:<game id>` of a copy in the same umbrella). Setting one takes that source's known value at
   once unless the field is locked. A metadata refresh leaves a field alone when its preferred
   source is another one that has a value; a preferred source with no value never blanks or blocks
   the field. `GET /api/game/{id}/field-sources` now also returns `preferred` and, per field,
   `sources`: the value each known source reports with the current one marked. Provider values are
   those a refresh recorded (the newest per provider); a refresh skipped because of a preference
   does not record its value, so that provider's last applied value is what the list shows.
3. **Implemented.** `GameFieldSource.vue` shows a compact label beside developer and publisher,
   description, tags, features, series and age rating only when sources disagree; it opens the list
   with "Prefer this source" and "Clear preference". The Sources summary gains a "Metadata" row
   (the most-used preferred source and the fields that disagree).

Each step is independently shippable; step 1 alone answers the provenance half of #348.
