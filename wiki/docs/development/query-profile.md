# Media and account query profile

Task [#260](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/260). The rule: **measure first, change only what a
measurement shows is a problem, and add no speculative indexes or caching.** The game-identity paths were already profiled at
5 000 and 20 000 games (#103/#205, and the live-game-list index); this page covers what they did not: the movie, TV and anime
libraries and the account tables.

## How to reproduce

```sh
cd src/backend            # a scratch, migrated PostgreSQL database; never real data
TEST_DATABASE_URL=... python ../../tools/profile_media_queries.py --titles 20000 --noise-users 400
```

The tool seeds one heavy account plus many light ones, runs each request through the real `library_page`, counts the SELECT
statements, and runs `EXPLAIN (ANALYZE, BUFFERS)` on every one, flagging sequential scans of large tables. It deletes its rows
when it finishes.

## Data shape

20 000 titles of each kind for one account (movies, TV shows, anime; TV and anime with one season row each) plus 400 other
accounts holding 200 titles of each kind, so the library tables hold 100 000 rows each. Ratings on 60 % of rows, 1 to 3 genres
each, statuses spread across all values.

## Library requests (20 000 titles for the account, 100 000 rows in the table)

Page size 100. "Slowest" is the slowest single statement; the total includes loading the page's rows and seasons.

| Kind | Request | Statements | Total | Slowest | Large scans |
| --- | --- | --- | --- | --- | --- |
| Movies | title order | 4 | 232 ms | 50 ms | none |
| Movies | by rank | 4 | 340 ms | 89 ms | none |
| Movies | by progress | 4 | 331 ms | 78 ms | none |
| Movies | search `dark` | 4 | 539 ms | 98 ms | none |
| Movies | genre filter | 4 | 343 ms | 71 ms | none |
| Movies | status tab | 4 | 402 ms | 64 ms | none |
| TV | title order | 6 | 379 ms | 88 ms | none |
| TV | by progress | 6 | 572 ms | **380 ms** | none |
| TV | search, genre, status, rank | 6 | 316 to 588 ms | 52 to 111 ms | none |
| Anime | title order | 6 | 571 ms | 67 ms | none |
| Anime | by progress | 6 | 652 ms | 174 ms | none |
| Anime | search, genre, status, rank | 6 | 281 to 492 ms | 64 to 89 ms | none |

Findings:

* **Every request uses the `user_id` index.** There is no sequential scan of a large table. (With a single dominant account and
  few others, PostgreSQL correctly prefers a sequential scan because the index is not selective; that is the right plan, not a
  problem.)
* **Statement counts are constant per request** (4 for movies, 6 for TV and anime: rows, status counts, total, score ranks,
  and the season loads), independent of library size. There is no per-row querying.
* **The slowest request is TV "by progress" at 380 ms for a 20 000-title single library.** It computes a per-show episode ratio
  with a correlated subquery. This is far above realistic libraries and still under half a second, so **no change was made**:
  it has no measured user impact, and rewriting it would change tested ordering semantics.

## The one measured problem: deleting a movie or TV show

`anime.linked_movie_id` and `anime.linked_tv_show_id` are foreign keys with `ON DELETE SET NULL` and were not indexed. To delete a
movie or TV show PostgreSQL must find the anime that link to it, which without an index is a scan of the whole `anime` table
**per deleted row**. Measured with 100 000 anime rows:

| | Delete 300 movies | Delete 300 TV shows |
| --- | --- | --- |
| Before (no index) | **9.5 s** | the same shape |
| After (partial indexes) | **0.42 s** | 0.53 s |

Deleting a whole account cascades through every title it owns, which is the same cost multiplied: the profiling tool's own
clean-up of 401 accounts (300 000 titles) ran for over 30 minutes before the change and finished in seconds after it.

**Change:** two additive, partial indexes (`WHERE ... IS NOT NULL`, because almost every row has no link) in one Alembic
revision, `a4c7e19b52d8`, safe to replay, with a downgrade that drops them. Ownership and filter semantics are unchanged. The
test `test_anime_link_indexes.py` upgrades, replays and downgrades the migration on a real PostgreSQL schema.

## Account tables

| Lookup | Index | Verdict |
| --- | --- | --- |
| Session by token (`user_sessions.token_hash`) | unique | already indexed |
| Sessions of a user, expiry sweeps (`user_id`, `expires_at`, `last_seen_at`) | each indexed | already indexed |
| Invitation by email digest and expiry (`email_digest`, `expires_at`) | each indexed | already indexed |

No change.

## Not changed on purpose

No new index on `genres` (the array containment filter was fast at 100 000 rows), no caching layer, no query rewrite. Re-run the
tool when a table or access pattern changes; if a new request shows a sequential scan of a large table or a statement count that
grows with library size, that is the evidence to act on.
