# Design: book tracking and future media types

Status: **proposal for review** (task [#265](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/265), part of
[#248](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/248)). Nothing here is implemented. It compares ways
to add books and recommends a first deliverable; other media types stay separately scoped.

## 1. Where the application is today

Each media kind is its own vertical slice, with a small shared core:

| Layer | Games | Movies, TV, anime |
| --- | --- | --- |
| Model | `Game` plus the identity model (observations, counters, copies) | `Movie`, `TVShow` (+`TVSeason`, `TVEpisode`), `Anime` (+ the same), each with `user_id`, `status` enum, `rating_overall`, `genres`, `sort_title`, `deleted_at` |
| Routes | `routes/games*.py`, `game_identity*.py` | `routes/movies.py`, `tv_shows.py`, `anime.py` on top of `media_common.py` (`LibraryQuery`, `LibraryFilters`, `library_page`) |
| Providers | core Steam/IGDB/SteamGridDB hardcoded; optional ones are plugins | TMDB/OMDb/TVmaze/AniList/AniZip hardcoded; optional ones are plugins |
| Contract | `MediaType` enum (`game`, `movie`, `tv_show`, `anime`) in `plugin_api/metadata_contracts.py` | same enum |
| UI | `GameLibrary`, `GameDetail`, `useGameLibrary` | `MovieLibrary`, `TVShowLibrary`, `AnimeLibrary` over the shared `MediaLibraryView` and `useMediaLibraryPage` |

There is **no generic "media item" abstraction** and this proposal does not introduce one: the shared parts that exist
(`media_common.py`, `MediaLibraryView`, the list/stat helpers) are reused where they already fit.

## 2. What a book needs that the others do not

* **Work versus edition.** "Dune" is one work with many editions (hardcover 1965, mass-market 2005, audiobook). A reader
  tracks the *work* ("I read Dune") but owns and reads *editions* (pages differ).
* **Identifiers:** ISBN-10/13 per edition, plus work-level ids (Open Library `OLxxxW`, Google Books volume id, Goodreads id).
* **Progress:** pages (edition-dependent), percent (edition-independent), or finished/not. Audiobooks use time.
* **People:** several authors, translators, narrators.
* **Series:** book N of a series (position can be fractional: 0.5, 2.5).
* **Rereads** as dated reading sessions, not a counter.

## 3. Options

| Option | What | For | Against |
| --- | --- | --- | --- |
| **A. A fourth slice** (`Book`, `BookEdition`, `ReadingSession`) | follows the existing pattern | no rewrite, isolated migration, can reuse `media_common` for lists/search/sort | duplicates some boilerplate (accepted: it is how movies/TV/anime coexist) |
| B. Generic media table with JSON "attributes" | one table for everything | one UI | loses typed progress and constraints; a framework rewrite; breaks existing contracts |
| C. Books only as a plugin | optional | keeps the host small | needs host-owned library storage; plugins have no library tables (see the plugin storage rules); fragments the data |

**Recommendation: A**, with optional *metadata providers* as plugins (like other optional sources), and **no** change to
existing media tables or APIs.

## 4. Proposed first deliverable

**"Track the books I read and want to read, with manual entry plus ISBN lookup."** Nothing else.

### 4.1 Data

```text
books                       book_editions                  reading_sessions
-----                       -------------                  ----------------
id, user_id, deleted_at     id, book_id -> books           id, book_id -> books
title, sort_title           isbn13 (nullable, normalised)  edition_id -> book_editions (nullable)
authors (text[])            isbn10 (nullable)              started_on, finished_on (dates, nullable)
series_name, series_position  format: print|ebook|audio    progress_kind: pages|percent|minutes
description                 pages (nullable)               progress_value (nullable)
first_published_year        publisher, published_on        status (reading|finished|dnf)
cover_url (local cache)     language                       rating (0-10, nullable)
status: WISHLIST|TO_READ|READING|FINISHED|DNF|REREAD     note (text)
rating_overall, favorite, genres (text[]), provider_ids (jsonb)
progress_kind, progress_value (current, denormalised from the open session)
```

* `books` mirrors the columns the other kinds share (`user_id`, status enum, `rating_overall`, `genres`, `sort_title`,
  `deleted_at`) so `library_page`, soft-delete/trash, favourites and lists work unchanged.
* **Editions are optional.** A book with no edition is complete. Progress in *percent* works without one; *pages* needs an
  edition with `pages` (otherwise the UI says so instead of guessing).
* **Rereads** are rows in `reading_sessions`; the book's status is derived from the open session, not stored twice.
* One additive migration; single Alembic head; downgrade drops the three tables.

### 4.2 API and UI (reusing what exists)

```text
GET/POST/PATCH/DELETE /api/books...          same shape as /api/movies...
GET  /api/books/lookup?isbn=...              provider lookup (see 4.3)
POST /api/books/{id}/sessions                start/finish/update a reading session
```

UI: `BookLibrary` over `MediaLibraryView` (list, search, filters, "best match" sort), `BookDetail` (cover, authors, editions,
sessions, progress). Add-book flow: ISBN, title search, or manual.

### 4.3 Providers

* **Core:** none required for the first deliverable beyond **Open Library** (public, keyless, ISBN and title search),
  hardcoded like the other core sources and listed under **Settings › Metadata** with the same availability notes.
* **Optional:** Google Books, Hardcover and others as *metadata provider plugins* through the existing public contract; this
  needs `MediaType.BOOK` added to the contract enum as an additive, versioned change (old plugins ignore it).
* The "no provider configured" experience follows the pattern of [#254](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/254).

## 5. Examples the design must handle

1. **Multiple editions.** "Dune": hardcover (612 pages) and mass-market (896). A reader on the paperback at page 448 is 50%;
   switching edition keeps *percent* and recomputes pages. Finished once, read again later = two sessions, status REREAD.
2. **Incomplete metadata.** An ISBN with no page count: status and percent work; page progress is unavailable and says why.
   A manual book with no authors is valid.
3. **User corrections.** A wrong author from a provider is edited locally and **locked** (the same lock model as
   games/movies) so a later refresh never overwrites it; the raw provider value stays recoverable.
4. **Series.** "Book 2.5" sorts between 2 and 3; two books with the same series name and different positions group.
5. **Audiobook plus print.** Two editions of one work, one session each, one finished date.

## 6. Import, export, search

* **Export/restore:** books, editions and sessions join the library export (versioned section `books`); restore follows the
  existing skip-if-same-title-and-year rule, with ISBN as the stronger key.
* **Import:** Goodreads and StoryGraph CSV importers, each with the existing preview-and-choose pattern, as **separate** later
  tasks.
* **Search:** library search and the metadata search reuse the existing relevance-ordered search.

## 7. Plugin-contract and migration effects

* `MediaType` gains `book` (additive; the contract version rule for additive enums needs a short review).
* No existing table, API or plugin contract changes behaviour. Existing plugins keep working; book providers declare
  `media_types: ["book"]`.
* Nothing is copied wholesale from another feature: the new slice reuses `media_common`, the shared library view and the lists
  and stats helpers; book-specific code is the model, session logic and detail page.

## 8. Test plan (planned, not written)

Authorization (user A cannot read/modify user B's books or sessions, including editions and sessions by id); round trip
(export → restore → identical); edition switching keeps percent; reread creates sessions not duplicates; ISBN-10/13
normalisation and checksum; soft delete and trash; migration up and down on a populated database; provider failure and
"not configured" paths; locked fields survive refresh.

## 9. Non-goals and open decisions

**Non-goals:** comics/manga, podcasts, music, board games (each needs its own scoping); social features; DRM or ebook
reading inside the app; automatic ownership detection; a generic media framework.

| Decision | Recommendation |
| --- | --- |
| Slice (A) or generic (B) | **A**; revisit generic only after two more kinds exist and share real code |
| Work/edition in v1 | work + optional editions; sessions in v1 (rereads are a core reading behaviour) |
| First provider | Open Library (keyless) |
| Where books show on Home/Calendar | not in v1 (a later widget; the calendar has no book dates) |
