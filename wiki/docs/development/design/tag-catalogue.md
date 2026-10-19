# User-extensible tag catalogue without losing source tags

Design proposal for [#267](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/267)
(source: Rosefall #36). **Design only.** Items marked **OPEN QUESTION** need a maintainer decision. Paths start
`src/backend/src/`.

## 1. Problem and current state (reviewed first, as the issue asks)

* Storage: tags are `ARRAY(String)` columns on `games` (`database/models/game.py`), `anime`, `tv_show`, `movies`,
  `game_archives`, `game_file_items`, `game_note_details` and `media_items`. The user owns the row, so tags are
  already per user.
* Writers: Steam player tags (`api/routes/steam_tags_refresh.py`, which keeps every tag a game already had that is
  not a picked Steam tag, compared case-insensitively); library sync appends the source name
  (`api/routes/library_sync.py`); MAL import merges with `dict.fromkeys` (`features/imports/mal_apply.py`); plugins
  pass `tags` through `plugin_api/game_import.py`; notes, archives and files use `game_notes.clean_tags` (trim,
  collapse whitespace, 40 characters, case-insensitive de-duplication).
* Readers: top tags via `func.unnest(Game.tags)` in `api/routes/stats.py`; the media tag filter via
  `MediaItem.tags.any(tag)` in `api/routes/media.py`; export and import carry the arrays.
* #140 / [#177](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/177): the Games filter compares tags
  through fixed client-side rules (`utils/genres.ts`: spelling, synonyms, broader genres). Stored tags are never
  rewritten, "Roguelike" and "Roguelite" stay separate, and rollback is removing a rule entry (see
  [Games user guide, Tag normalization](../../user-guide/games.md)). That work is not reopened here.
* Gap: the array does not record **who or what supplied each tag**. Field provenance exists elsewhere
  (`game_field_values` with `FieldValueOrigin` in `game_identity_tracking.py`, and source observations), but a flat
  list cannot say "Steam gave this tag, I typed that one". Today the only protection is behavioural (the Steam
  refresh keeps non-Steam tags).

## 2. Goals and non-goals

Goals: let a user keep their own vocabulary (display label, alias, grouping) while every raw source tag stays
recoverable and every existing filter, import and export keeps working.

Non-goals: changing #140's filter rules; silent, LLM-generated or regex-wide merging; a shared global tag service
or moderation queue; changing what plugins read and write (raw tags); rewriting provider data.

## 3. Evidence required before any new table

Retaining current storage is a valid outcome. A table is justified only by a measured or product need. Before
implementation, record on the maintainer's library and on a synthetic 5 000-game / 800-distinct-tag library:

1. Query time of the top-tags `unnest` and the tag picker data with the current arrays.
2. Count of tags within one user that differ only by case, whitespace or punctuation.
3. Whether users actually ask for rename or alias (product evidence from issues).

**Recommendation:** do not replace the arrays. If evidence 2 or 3 supports it, add a small per-user
**alias and label layer beside them**; the arrays stay the source of truth for what a record carries.

## 4. Proposed data model (additive, only if approved)

`user_tags` (per-user catalogue; `user_id` FK, cascade):

| Column | Notes |
| --- | --- |
| `id`, `user_id`, `label` (<=40) | Display name |
| `key` | Comparison key (NFKC, casefold, collapsed whitespace); unique `(user_id, key)` |
| `kind` | `user` (created here) or `source` (optionally discovered from existing data) |
| `namespace` | Optional prefix seen in data (`Genre`, `Steam`, ...), kept as metadata and never parsed destructively |
| `created_at`, `archived_at` | Soft delete |

`user_tag_aliases(user_id, alias_key, tag_id)`, unique `(user_id, alias_key)`: an explicit, user-created mapping
"treat raw tag X as catalogue tag Y". It is **applied at read time** (filters, statistics, picker) and never by
rewriting `games.tags`. No association table in phase 1: the arrays still hold raw values, so there is nothing to
backfill and rollback is dropping two tables. An association table is considered later only if section 3 shows the
arrays are a bottleneck (**OPEN QUESTION 1**).

## 5. Semantics

* **Visibility.** Per user only. Shared defaults are not in phase 1; a deployment-level default list would be a
  separate read-only table users opt into (**OPEN QUESTION 2**).
* **Collisions.** `key` folds case, Unicode normalisation (NFKC) and whitespace, so two labels with one key are
  one catalogue tag (creating the duplicate returns the existing one). Namespaced forms (`Genre: Action`) are not
  merged with `Action` automatically; that comparison belongs to #177's read-only rules. An alias is required.
* **Rename.** Changes `label` only; aliases and raw arrays are untouched.
* **Delete.** Archives the catalogue tag and its aliases. Raw tags remain and reappear as plain tags. Removing a
  tag from a game edits that game's array only, as today.
* **Alias versus rewrite.** An alias is reversible and user-authored. Rewriting stored tags is out of scope, so a
  Steam, plugin or import refresh never fights the user: it may add raw tags, which resolve through the aliases.
* **Suggestions.** The editor may propose alias candidates (same key, small edit distance) for the user to accept
  one at a time. Nothing applies silently, and no model-generated or regex-wide merge runs.
* **Export and import.** Exports gain an optional `tag_catalogue` section. Importing an older file without it is
  unchanged and older builds ignore the section. The Plugin API is unchanged.

## 6. API and UI sketch

`GET/POST/PATCH/DELETE /api/tags` and `/api/tags/{id}/aliases`, all `get_current_user`-scoped. The picker and
statistics endpoints add an alias-resolution step to their existing queries. UI: a "Tag catalogue" page in
settings (list, rename, alias with a preview of affected game counts, delete). The existing compact picker shows
a catalogue label with "includes N raw spellings", in the style #177 already uses.

## 7. Migration, backfill and rollback

Upgrade creates two empty tables (additive, single linear head); no data moves. An optional user-triggered
"discover" action creates `kind=source` rows from distinct current tags. Rollback is a downgrade that drops the
tables; tags, filters, imports and exports behave exactly as before because the arrays were never modified.
Populated-database test: every `tags` array is identical before upgrade, after upgrade, after downgrade and after re-upgrade.

## 8. Alternatives considered

* **Global `tags` plus `game_tags` tables (the upstream suggestion):** backfills eight array columns, creates a
  second source of truth that plugin and import writers must keep in sync, and forces a global-versus-user
  decision without a measured benefit. Rejected for phase 1.
* **Rewriting arrays on rename or merge:** destroys source provenance and fights the Steam refresh. Rejected.
* **Keep arrays only:** valid if evidence 2 and 3 are weak; the decision should follow the evidence.
* **Moving #177's rules server-side:** duplicates a fixed rule set for no gain.

## 9. Phasing

1. Evidence gathering (section 3) and a maintainer decision; no code.
2. Tables, alias resolution in picker and statistics queries, API, backend tests.
3. Catalogue settings UI and the export/import section.
4. Shared defaults or an association table, only on evidence.

## 10. Test strategy

* Key folding: case, NFKC, combining marks, CJK, whitespace, emoji. Namespaces are not merged. Alias cycles and chains are rejected.
* API: per-user isolation (foreign ids 404), duplicate create returns the existing tag, rename keeps aliases, delete archives.
* Regression with #140/#177: raw arrays unchanged after every catalogue operation; Steam refresh with aliases keeps
  user tags; MAL and plugin imports resolve through aliases; export round trip with and without the catalogue section.
* Migration: populated upgrade and downgrade, single head.

## 11. OPEN QUESTIONS for the maintainer

1. Is the evidence in section 3 enough to proceed, or should the outcome be "keep the arrays, no catalogue"?
2. Per user only (proposed), or also an optional deployment-level default catalogue?
3. Should the catalogue cover tags on notes, files, archives and media items, or games and library media only?
4. Are alias suggestions (edit-distance hints, confirmed one by one) acceptable, or too close to automatic merging?
5. May a catalogue label differ from every raw spelling (pure rename), or only choose among them?

## 12. Acceptance-criteria mapping

| Issue criterion | Where |
| --- | --- |
| Reason for any new table, or retaining storage as an outcome | Sections 3, 8 |
| Per-user visibility, shared defaults, case/Unicode/namespace collisions, delete/rename | Section 5 |
| Populated migration/backfill and rollback preserving tags, filters, imports, exports | Sections 4, 7 |
| No silent LLM/regex merging; concrete phases and tests linked to #140 | Sections 1, 5, 9, 10 |
