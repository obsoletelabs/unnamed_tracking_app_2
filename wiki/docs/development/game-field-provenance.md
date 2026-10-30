# Per-field provenance and a preferred metadata source (design)

Design for [#348](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/348) and the
metadata half of [#349](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/349).
**Nothing in this document is implemented yet.** Paths start `src/backend/src/`.

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
the existing closed set (`user`, `source`, `metadata`, `legacy`, `system`); for `metadata` the
provider is identified through the observation, so no new column is needed for a first version.

Fields in scope: `description`, `developer`, `publisher`, `genres`, `tags`, `release_date`,
`series`, `age_rating`, `features`, `cover`/artwork choice. Excluded: ids, `provider_ids`,
timestamps and everything already tracked elsewhere.

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

1. Write provenance rows for the in-scope fields during refresh and manual edit (backend only,
   behind the existing API; `GET /api/game/{id}` gains an optional `field_sources` map).
2. Add `game_field_preferences` and apply the precedence above on refresh (migration, tests for
   lock > preference > ranking and for a preferred source that lacks a value).
3. Page UI: source label, value list, Prefer action, Sources-summary row.

Each step is independently shippable; step 1 alone answers the provenance half of #348.
