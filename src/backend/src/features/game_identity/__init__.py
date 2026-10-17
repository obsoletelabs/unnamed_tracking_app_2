"""Reversible game identity merges, splits and durable user decisions (#95).

A merge is a `same_game` relationship plus a recorded decision; nothing is
deleted or rewritten, so ending the relationship restores the independent
views. See `wiki/docs/development/game-identity-decisions.md`.
"""
