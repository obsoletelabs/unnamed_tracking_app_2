# Design: a user-extensible tag catalogue

Status: **proposal for review** (task [#267](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/267), part of
[#248](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/248)). Nothing here is implemented. It does not reopen
the completed compact-filter work in [#140](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/140).

## 1. Current state (reviewed first)

* **Storage.** Tags live as arrays on the records themselves: `Game.tags` and `Game.features` are `ARRAY(String)`; movies, TV
  and anime keep `genres` (and `tags`) as arrays. There is no tag table. Provider tags, imported tags and tags a person typed
  share the same array, with no record of which is which beyond the *source observation* a game can carry
  (`ObservedFacts.tags`, `source_updated_at` in the identity model).
* **Normalisation (#140).** Tags are stored **exactly as written**. Filters read them through three deterministic rules that
  run in the browser (`frontend/src/utils/genres.ts`): spelling keys (case, accents, spacing, punctuation, `&`, a known list
  of source prefixes), a short synonym table (`TAG_SYNONYMS`), and broader-genre membership (`BROADER_GENRES`, one-way).
  Related-but-different tags are deliberately not merged (`Roguelike` ≠ `Roguelite`). Rolling a rule back is deleting its
  entry; no data is rewritten.
* **Queries.** Library filters for media kinds are `model.genres.contains([g])` per selected genre
  (`media_common._library_statement`); game filters run client-side over the loaded library
  (`useGameLibrary`/`gameLibraryQuery`). Neither depends on a tag table.

So today the system has *no* catalogue, *no* per-user vocabulary and *no* way for a person to say "my `scary` means
`Horror`" other than editing every record.

## 2. What people are actually asking for

1. **Their own tags**, created once and reused with autocomplete, instead of free text each time.
2. **Their own aliases**: "treat `cozy`, `comfy` and `wholesome` as the same for *me*".
3. **Shared defaults** an administrator can offer to everyone.
4. **Never lose what a provider said.**

(1) and (2) are about a *vocabulary and a mapping*, not about changing records.

## 3. Options

| Option | Summary | Verdict |
| --- | --- | --- |
| **A. Keep arrays; add a per-user alias/vocabulary layer (recommended)** | records unchanged; a small `user_tags` table holds the user's chosen spellings and aliases; filters and autocomplete read through it | smallest schema change; raw tags always recoverable; matches #140's "never rewrite" principle |
| B. Full catalogue + association tables (`tags`, `game_tags`, …) | normalised many-to-many, tags are rows | needs a backfill of every record, three media kinds, imports, exports and plugin contracts; a query rewrite; **no measured need** (arrays + contains queries are fast, see the [query profile](../query-profile.md)) |
| C. Do nothing | keep #140 only | acceptable if people do not need aliases; leaves "my vocabulary" unsolved |

**Recommendation: A, in phases, starting with evidence.** Phase 0 measures whether people already create many near-duplicate
tags; if they do not, stop at C.

## 4. Proposed design (Option A)

### 4.1 Tables (additive)

```text
user_tag_aliases
  id, user_id -> users (cascade)
  alias_key        -- the spelling key of the alias (see 4.2); UNIQUE (user_id, alias_key)
  alias_label      -- as the person typed it
  target_label     -- the tag it means (a canonical choice or the person's own tag)
  created_at
user_tags            -- the person's own vocabulary (optional, phase 2)
  id, user_id, label, key  UNIQUE (user_id, key), media_scope (game|movie|tv|anime|all)
```

*Shared defaults* are the existing code tables (`TAG_SYNONYMS`, `BROADER_GENRES`) and, if an administrator needs to extend
them, a single `instance_tag_aliases` table with the same shape and `user_id` null. A per-user alias **wins** over a shared one.

### 4.2 Keys and collisions

Use the same spelling key function as #140 (`tagSpellingKey`: Unicode NFKD fold, drop combining marks, lowercase, remove
punctuation, `&`→`and`, source-prefix drop), implemented once in the backend as well, with **shared test vectors** so the two
implementations cannot drift.

| Case | Behaviour |
| --- | --- |
| `Sci-Fi`, `sci fi`, `SCI‑FI` | same key |
| `Café`, `Cafe` | same key (accent folding) |
| Namespaced `Steam: Roguelike` | prefix dropped for the key; the stored raw tag keeps it |
| Two aliases whose keys collide | rejected on create (409) with the existing alias shown |
| An alias that points at another alias | resolved one hop only; chains and cycles are refused on create |
| Alias that equals a canonical choice's key | refused (it would shadow a built-in; use a different label) |
| Emoji / non-Latin tags | folded by NFKD only; no transliteration; kept as typed |

### 4.3 Semantics

* **Applying an alias never rewrites records.** It changes how the *person's* filters, chips and autocomplete group tags.
  The raw tag on a game stays exactly as the provider or person wrote it and is always shown on the record.
* **Rename** of an alias changes only its label; **delete** removes the grouping and everything returns to separate choices
  immediately. There is nothing to roll back in the data.
* **Explicit alias ≠ automatic rewrite.** An alias exists only because a person (or an administrator, for shared defaults)
  created it. No language model, no regex-wide merge and no "suggest merges" job runs silently. A *suggestion* UI (show
  near-duplicates the person can accept one by one) is allowed, never automatic.
* **Visibility.** Aliases are per user and invisible to others. Exports include a user's aliases in their own section;
  restoring them is skipped when the same key exists.

### 4.4 Where it is read

Filters and chips already normalise through one function; extend it to consult the user's aliases (loaded once with
preferences). The backend genre filter for movies, TV and anime needs the same mapping: expand a selected tag to its alias
group before `genres.contains`. No new endpoint is required beyond alias CRUD and the preferences load.

## 5. Migration and rollback

* Phase 1 adds two tables; no data migration; downgrade drops them.
* Nothing is backfilled because nothing is rewritten. If Option B is ever chosen, it would need a populated-database plan:
  a read-only backfill that *adds* association rows while leaving the arrays authoritative for one release, a comparison
  job, and only then switching reads. That plan is out of scope until measurements justify B.

## 6. Test plan

Shared key vectors (frontend and backend); collision and chain/cycle refusal; per-user isolation (user A's aliases do not
affect B's filters or exports); filter equivalence (selecting an alias group finds records tagged with any member, across
games and the three media kinds); raw tags unchanged after create/rename/delete of aliases; export/import round trip;
imports that carry tags are unaffected; performance guard (an alias group of 50 members still uses the array query).

## 7. Phases

0. **Evidence:** a read-only report (script, no UI) of near-duplicate tag keys per user and the most common tags across media
   kinds, run on a real or demo database. Decides A vs C.
1. **Aliases for the current user** (backend table + CRUD + shared key function + filter integration for games).
2. **Aliases in media filters and autocomplete** for movies, TV and anime.
3. **Optional:** the user's own vocabulary table and shared defaults for administrators.
4. **Docs:** extend "Tag normalization" in the games guide.

## 8. Open questions

1. Are aliases per media kind or global? *Recommendation: global per user; scope only if a real conflict appears.*
2. Should administrators manage shared defaults in the UI? *Recommendation: not until a person asks; the code tables stay the
   source.*
3. Is the suggestion UI worth building? *Recommendation: only after Phase 0 shows real duplicates.*
