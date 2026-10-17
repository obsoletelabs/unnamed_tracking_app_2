"""Resumable, idempotent backfill of existing games into the identity model (#102).

Maps every `games` row to its place in an umbrella, its source observations and
links, relationships, decisions, field provenance and counters without changing a
`games.id` or deleting anything. See `wiki/docs/development/game-identity-migration.md`.
"""
