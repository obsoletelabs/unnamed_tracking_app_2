"""Source-agnostic, idempotent game ingestion (#94).

Every source (manual entries, Steam, Playnite, store plugins, future
integrations) reports what it knows as scoped, versioned source observations.
The observation registry stores that evidence and its provenance; it never
decides which game an observation is, and never merges or overwrites a user's
decision. See `wiki/docs/development/game-ingestion.md`.
"""
