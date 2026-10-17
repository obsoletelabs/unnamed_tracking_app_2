# Hybrid identity matching and the duplicate queue

Phase 4 of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#99](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/99). It describes the
**code that exists now**; paths start `src/backend/src/`. No migration: decisions, relationships
and observations from #93 to #96 hold everything.

## 1. Evidence, not titles

`features/game_identity/matching.py` is pure and compares two library records. Each assessment
lists what `matched`, what `conflicts` and what is `missing`, plus a confidence.

| Evidence | Used as |
| --- | --- |
| Normalized title (`title_key`) | Core; never sufficient alone |
| Release date, platform | Core; a known difference in platform or release year is a conflict |
| Edition (words such as Deluxe, GOTY, plus an observed edition) | Core; a differing observed edition is a conflict |
| Provider IDs (IGDB, Steam, ...) | Authoritative; a shared one corroborates, a different value in the same namespace is a conflict |
| Source record | Two records of one source with different external IDs are different copies |
| Playtime and last played | Only count when non-zero and identical |

Levels:

| Level | Meaning | Action |
| --- | --- | --- |
| `auto` | Title, release date, platform and edition equal, identical non-zero playtime and last played, both records **trusted** | Merged automatically (confidence 0.96, rule `same-copy-evidence` v1) |
| `review` | Title equal plus a corroborating release date, platform or shared provider ID; or a shared provider ID | Queued (confidence below 0.9) |
| `low` | Title only, or near titles (`Hades` vs `Hades II`, extra edition word) | Nothing stored, no prompt. The existing duplicate highlight still lists title matches |
| `conflict` | Any conflict above, or a grouped copy | Never merged or queued |

A record is *trusted* when it has an open link to an observation that is `available`, not
archived, on a connection that is not disconnected. Records with no observation yet (before the
#102 backfill) can only be queued. `run_matching(inventory_complete=False)` downgrades an `auto`
verdict to a queue entry, so an incomplete or failed inventory never merges. Automatic merging
runs only after a source reports a complete pull (`record_complete_inventory` for
RetroAchievements and PlayStation, and an applied Playnite inventory from
`POST /api/playnite/inventory`) and on the user's "Check again". A Playnite inventory that is not
applied (for a library that never reported anything) does not start matching.

## 2. Durable decisions and the queue

`features/game_identity/review.py` writes through #95's `associate_automatically`:

- `automatic_same` plus an active `same_game` edge is an automatic association.
- `unresolved` by an automatic actor is a **queue entry**. The queue is the decision table, so it
  survives refresh, sync retry and restart; matching again changes nothing.
- A pair a person touched (merge, keep separate, undo, a split) is skipped. Automatic matching
  can never override a user decision, and a rejected pair blocks the groups containing it. A
  *new* third record is compared on its own against each earlier record.
- Legacy `game_duplicate_dismissals` are honoured; keeping separate from the queue also writes the
  dismissal, and a `confirmed_separate` identity decision now hides a pair from the legacy list.

Actions (`/api/game-identity/review`, user scoped; 404 for foreign or already resolved entries,
409 when not open, 422 invalid target):

| Route | Effect |
| --- | --- |
| `POST /run` | Evaluate the library now (idempotent) |
| `GET /queue` | Open entries with confidence, evidence and per-field differences |
| `POST /queue/{id}/merge` | User merge (optional `target_id`) |
| `POST /queue/{id}/keep-separate` | Durable separate decision |
| `GET /automatic` | Automatic associations that can still be undone |
| `POST /automatic/{id}/undo` | Split and remember they are separate |
| `POST /automatic/{id}/keep` | Confirm; the banner clears |

## 3. Interface

`AutomaticMatchBanner` (library page and Settings, Library, Duplicates) shows a calm
"Steam and Playnite appear to be the same tracked copy" notice with **Undo** and **Looks right**.
`IdentityReviewQueue` lists queued pairs with **Inspect differences**, **Merge** and **Keep
separate**. The existing duplicate list and library highlight are unchanged apart from hiding
pairs already in the queue. Library views still read `games` directly, so a merge changes the
identity model, not yet what the library lists.
