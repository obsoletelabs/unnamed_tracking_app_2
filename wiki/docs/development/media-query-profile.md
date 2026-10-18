# Media and account query profile

Issue [#260](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/260) asked for
measurements before any new index is added to the media and account reads. This page records
what was measured, what was changed and what was deliberately left alone.

## Already profiled (not repeated)

| Path | Where |
| --- | --- |
| Game identity views, matcher, counters at 5,000 and 20,000 games | #103 / #205, `tests/test_game_identity_scale.py`, [Game Identity Acceptance](game-identity-acceptance.md) |
| Live game list index | #205 |
| Navigation and loading behaviour | #60 |

## Newly measured

Reproduce with a migrated, dedicated test database:

```text
PROFILE_REPORT=1 pytest tests/test_media_query_profile.py -s
```

The test generates two accounts that each hold 5,000 movies, 1,500 TV shows and 1,000 anime
(3 seasons of 10 episodes each, 75,000 episode rows in total), 300 browser sessions, 100
pending invitations and a manual and a smart list. It calls the real endpoints as the first
account, counts SQL statements and ORM rows materialised, and with `PROFILE_REPORT=1` prints
`EXPLAIN (ANALYZE)` for every SELECT. Tables are `ANALYZE`d first, as autovacuum would have
done. `PROFILE_MOVIES`, `PROFILE_SHOWS` and `PROFILE_ANIME` change the sizes. Without the
variable the same test asserts the statement and row ceilings below.

| Request | Before: statements / ORM rows | After: statements / ORM rows |
| --- | --- | --- |
| `GET /api/movie/list` (default, search, status+genre+score, year sort) | 4 / 1-50 | unchanged |
| `GET /api/tv/list` (default, progress sort) | 6 / 1,700 | 5 / 200 |
| `GET /api/anime/list` (default, progress sort) | 6 / 1,700 | 5 / 200 |
| `GET /api/lists` (manual list of 40 plus smart lists) | 97 / 180,040 | 14 / 2,865 |
| `GET /api/lists/{id}` (manual list of 40) | 44 / 40 | 5 / 40 |
| `GET /api/sessions/me`, `/api/sessions/admin` | 1 / 0 | unchanged |
| `GET /api/auth/user-invitations` (100 pending) | 101 / 0 | 1 / 0 |

Database time for the library list statements is 5-35 ms per request in total at this size.
The movie, TV and anime plans all use `ix_movies_user_id`, `ix_tv_shows_user_id` or
`ix_anime_user_id` (or a sequential scan of the one user's rows once most of a small table
belongs to that user), then a top-N sort of the user's rows. Season lookups use
`ix_tv_seasons_show_id` / `ix_anime_seasons_show_id`; episode lookups use
`ix_*_episodes_season_id`. Session reads use `ix_user_sessions_last_seen_at` and the user key.
The invitation list is capped at 100 rows. None of these plans is dominated by a missing
index, so **no index or migration was added**.

## What was wrong, and the fix

The costs were in ORM loading, not in the database plans:

* **Library cards loaded every episode.** `TVShow.seasons` and `TVSeason.episodes` are
  `lazy="selectin"`, so a page of 50 shows also loaded every season and every episode of
  those shows (1,700 ORM rows) although the card schema only has season progress.
  `library_page` now loads the seasons and refuses the episode relationship
  (`raiseload`), keeping the count at one statement per relationship.
* **Custom lists were quadratic in the library.** Each list re-read the title language,
  every smart list loaded each whole library into Python (with seasons and episodes), and
  each manual item was fetched with its own query. The title language is now read once,
  the status, favourite and minimum-score rule parts are applied in SQL (the genre match
  stays in Python because it is case-insensitive over an array), season and episode rows
  are not loaded, and manual items are fetched with one query per media type. Results are
  unchanged: `test_lists_and_library_cards_keep_their_semantics` compares a smart list with
  a Python evaluation of the same rule.
* **Pending invitations queried the delivery status per row.** They are now read with one
  `IN` query.

## Left alone

* A smart list still materialises the titles it matches. At 2,865 rows for two lists this is
  bounded by the library, not by the number of lists; pushing the genre match into SQL or
  paginating lists would change the API and was not justified by a measured cost.
* No caching layer was introduced.
* The game paths are covered by the earlier measurements.
