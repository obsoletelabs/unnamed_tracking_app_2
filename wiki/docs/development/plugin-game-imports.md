# Plugin game imports

Plugin API contract **1.1.3** adds `games.import`, requiring a live `games.write`
grant for the installation and authenticated user. Declare at least 1.1.3 so an
older host rejects the plugin before it invokes an unsupported method. The v1
wire protocol and existing capabilities retain their behavior.

Provider connection, library paging, credentials and remote HTTP belong in the
plugin repository. The host accepts a bounded batch after that work completes;
it does not perform provider requests inside the import transaction.

```python
request("games.import", "games.write", {
    "source_label": "Example store",
    "source_scope": "account-id:library",
    "items": [{
        "external_id": "namespace:catalog-item-id",
        "title": "Selected game",
        "description": "Provider description",
        "provider_ids": {"store": "namespace:catalog-item-id"},
        "playtime_seconds": 1200,
        "available": True,
    }],
})
```

The payload rejects unknown fields. It contains 1–25 items with distinct external
IDs. `source_label` is a display label of at most 50 characters; `source_scope`
and each external ID are nonblank strings of at most 256 characters. The host
stores the complete external ID, including compound namespace/item identities;
it never truncates IDs to match or replay imports. Apply migration
`c696194f78fd` before importing IDs longer than 64 characters. Its downgrade
refuses to narrow the column while longer IDs exist, preserving those identities.
Titles are
nonblank and at most 500 characters. Optional fields are `description` (20,000),
`developer`/`publisher` (200 each), `tags` (50 strings of at most 128 characters),
`provider_ids` (32 entries, lowercase namespace keys and nonblank IDs),
`playtime_seconds` (an integer from 0 to 2,000,000,000) and `available` (boolean).
Omitted metadata and playtime are left unchanged.

## Identity and local state

The stable game ID includes plugin ID, authenticated user, source scope and
external ID. Changing a title or display label does not duplicate the game.
Identically named games, other plugins, other users and other source scopes
remain separate. Plugins cannot choose a target game ID or user ID, and title
matches are never adopted automatically. The response includes the saved game ID;
this operation imports metadata and progress, with artwork enrichment handled
separately by the host's existing game metadata flow.

Imported metadata respects locked fields, including title and its derived sort
title. Imported playtime only increases and can also be protected by a field
lock. New games start in Backlog, or Played when reported playtime is positive.
Subsequent available imports also advance eligible Backlog/Wishlist games using
fresh reported playtime. `available: true` establishes ownership, so Wishlist
becomes Backlog when no playtime is supplied; a locally saved positive playtime
value alone does not imply provider progress. Explicit positive provider playtime
can advance an eligible game even when the stored playtime field is protected.
Every existing Played, Playing, On Hold, Beaten, Dropped or Mastered status stays
unchanged. Notes, ratings and other personal state remain intact. This policy is
enforced by the host without changing the public request/response schema or SDK.
The source label is set on creation and later manual source edits are preserved.

An already-deleted record returns `conflict: "deleted"`; it is not restored or
duplicated. A locally changed external identity returns
`conflict: "identity_changed"` and receives no updates. Handle these conflicts
without repeatedly trying to create another row.

## Availability and retries

`available: false` marks an existing imported game unavailable without changing
its metadata or playtime. It never deletes the game, and an unknown identity
returns `skipped: "not_imported"` with a null ID. A successful later inventory
clears the unavailable flag. Only report missing items after a complete, valid
remote inventory; provider errors or partial paging are not proof of removal.

The response is `{ "games": [...] }`. Every entry identifies `external_id`,
`id` and `created`; successful entries also return `title`, `status`,
`playtime_seconds` and `available`. Conflicts/skips have the fields described
above and omit the success state. No filesystem paths or credentials are returned.

Batches validate before database work. Concurrent plugin imports for one user
serialize only their short database transactions; retrying a completed or
interrupted batch retains its identities. Standard host folder allocation and
creation remain in the existing import service, with identity-specific names for
plugin-created games. No new tables or migrations are needed.

## Owned copies

Users may explicitly group imported records under a main game using the host's
`owned_copy` relationship. Grouping retains the original IDs, external identities,
provider IDs and folders. `games.import` still updates the same store copy and
does not overwrite the main game's personal tracking. Separation clears the
relationship without recreating records. Plugins need no new capability or API
version to remain compatible.
