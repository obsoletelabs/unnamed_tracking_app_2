# Game identity decisions, merges and splits

Phase 2 of the game identity overhaul
([#91](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/91)), delivered for
[#95](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/95). It builds on the
tables from the [data model](game-identity-model.md) (#93) and the
[observation registry](game-ingestion.md) (#94). Code: `features/game_identity/`
(`graph.py`, `decisions.py`) and `api/routes/game_identity.py`. No migration: the schema
already holds everything.

## 1. Rules

1. **A merge is a relationship plus a decision.** `merge_identities` adds an active
   `same_game` edge between the two groups and a `confirmed_same` decision. No `games`
   row, observation, link, counter or adjustment is changed or deleted, so the merge is
   undone by ending the edge. Effective identity is resolved by following the edges
   (`resolve_identity`).
2. **Unmerge ends the edge** (kept with `ended_at`) and supersedes the decision. The group
   that hung under the detached identity moves with it. Decisions that joined the two
   halves are reset to `unresolved`; the explicit pair becomes `confirmed_separate` unless
   the caller passes `keep_separate=false`.
3. **User decisions outrank automation.** Only `user`/`migration` actors can confirm
   (also enforced by the database). `write_decision` refuses to let an automatic actor
   replace a confirmed decision. `associate_automatically` returns `blocked` when a user
   rejected any pair joining the two groups and `unchanged` when the pair is already
   decided. A user merge of an automatically associated pair upgrades it to
   `confirmed_same`.
4. **Rejected pairs are durable.** `confirmed_separate` is a row, so it survives
   restarts and re-imports, and automatic matching and the legacy "keep both" review
   (`/api/game-duplicates/keep-both`, which now also writes the decision) honour it. The
   decision is about that pair (and the groups it joins). A new third record is compared on
   its own: it can be associated with either side, but never in a way that would join the
   rejected pair.
5. **Unresolved candidates** are `unresolved` decisions from `associate_automatically(...,
   merge=False)`. They are recorded once per rule version and never after a rejection.
6. **Provenance.** Each decision stores actor kind and detail, reason, rule version,
   confidence, evidence, `prior_state`, `resulting_state` and timestamps. Decisions are
   superseded, never edited, so the full history is available
   (`GET /api/game-identity/decisions?include_superseded=true`).
7. **Moving observations.** `move_observation` ends the open link and opens a new one via
   `relink_observation`; the external ID, revisions and earlier links stay. The old pairing
   is saved as a rejected observation decision (`observation_match_rejected`) so a matcher
   cannot move it back; a later explicit move back replaces that decision. Ingestion never
   re-attaches a linked record, so repeated Playnite syncs after a merge, split or move
   change nothing.
8. **Relationship validation.** `add_relationship` rejects self edges, unknown kinds, wrong
   labels and, for kinds that are meaningless when circular (`same_game`, version, remake,
   remaster, port, DLC, expansion, mod kinds), any edge that closes a cycle. `related_to`,
   `custom`, series and collection kinds may be circular: unusual user groupings are
   allowed. Edges are ended, not deleted.
9. **Concurrency.** Every change takes a per-user PostgreSQL advisory lock
   (`lock_user_identities`) held until the transaction ends, so simultaneous syncs, user
   edits and automatic matching apply one after another; repeats are idempotent. The unique
   indexes from #93 remain the backstop.
10. **Isolation.** Every query is filtered by `user_id`; another user's game, observation or
    relationship is `LookupError` (HTTP 404) and the composite keys reject it in the
    database too.

## 2. API

| Route | Effect |
| --- | --- |
| `POST /api/game-identity/merge` | `{source_id, target_id, reason?, replace_separate?}`; 409 if the user previously kept them separate |
| `POST /api/game-identity/unmerge` | `{game_id, keep_separate?, reason?}`; 409 if the game is not merged |
| `POST /api/game-identity/separate` | `{first_id, second_id}`: splits if merged and records the rejection |
| `POST /api/game-identity/observations/{id}/move` | `{game_id}` |
| `GET /api/game-identity/games/{id}` | effective identity |
| `GET /api/game-identity/decisions` | current (or all) decisions |
| `POST /api/game-identity/relationships`, `.../{id}/end` | typed edges with cycle validation |

Playnite routes and payloads are unchanged (compatibility gate, #100): the tests in
`tests/test_game_identity_decisions.py` repeat Playnite syncs before and after a merge and
an unmerge and assert that observations, revisions and links are identical.

## 3. Not in this change

Counter reconciliation across a merge (#96), field quality (#98), the matching rules that
call `associate_automatically` (#99), the library UI (#101) and the backfill of earlier
`owned_copy` groups and dismissals (#102). Library views still read `games` directly, so a
merge does not yet change what the library shows.
