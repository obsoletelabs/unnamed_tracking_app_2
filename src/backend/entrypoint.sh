#!/bin/sh
set -e
  
echo "Starting application..."

# Wait for PostgreSQL and serialize migration startup. Populated upgrades require
# verified pre-upgrade recovery evidence; unfamiliar histories stop for review.
# See wiki/docs/deployment/migration-recovery.md for preparation and repair.
echo "Preparing database..."
python -m src.database.migrate

# Maps existing games onto the game identity model (#102). Additive, resumable and
# idempotent, so it runs beside the server, picks up where an interrupted run stopped
# and never blocks or fails start-up. Set GAME_IDENTITY_BACKFILL=off to run it yourself:
#   python -m src.features.game_identity_backfill [--user ID] [--dry-run]
# See wiki/docs/development/game-identity-migration.md (take a database dump first).
if [ "${GAME_IDENTITY_BACKFILL:-auto}" != "off" ]; then
    (python -m src.features.game_identity_backfill >/tmp/game-identity-backfill.json 2>&1 \
        || echo "Game identity backfill did not finish; it resumes on the next start.") &
fi

echo "Starting API server..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8000
