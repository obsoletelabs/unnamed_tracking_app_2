"""Scheduled automatic backups with no new worker container and no
new dependency. The central schedule manager (src/features/jobs.py) runs it, so the
interval and the on/off switch are administrator settings. It writes
one JSON snapshot per user in the same shape as the manual Export/Import
feature (src/api/routes/export_import.py), so a backup file can be fed
straight back through POST /api/import/library if it's ever needed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select

from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports.library_export import build_library_export

logger = logging.getLogger(__name__)

_BACKUP_ROOT = Path("/data/backups")
BACKUP_INTERVAL_SECONDS = 24 * 60 * 60
BACKUPS_TO_KEEP_PER_USER = 7


async def run_backup_for_user(user_id) -> Path | None:
    async with SessionLocal() as db:
        snapshot = await build_library_export(db, user_id)
        if (
            not snapshot.games
            and not snapshot.movies
            and not snapshot.tv_shows
            and not snapshot.anime
        ):
            return None
        payload = snapshot.model_dump(mode="json")

        user_dir = _BACKUP_ROOT / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        backup_path = user_dir / f"backup-{payload['exported_at']}.json"
        backup_path.write_text(json.dumps(payload))

        # keep only the most recent N, this is a rolling safety net, not a
        # long-term archive (that's what the manual Export button is for)
        existing = sorted(user_dir.glob("backup-*.json"), reverse=True)
        for stale in existing[BACKUPS_TO_KEEP_PER_USER:]:
            stale.unlink(missing_ok=True)

        return backup_path


async def run_all_backups() -> dict[str, int]:
    async with SessionLocal() as db:
        user_ids = [row[0] for row in (await db.execute(select(User.id))).all()]

    written = 0
    failed = 0
    for user_id in user_ids:
        try:
            if await run_backup_for_user(user_id):
                written += 1
        # Isolate failures between users and keep the periodic backup worker alive.
        # pylint: disable-next=broad-exception-caught
        except Exception:
            logger.exception("Backup failed for user %s", user_id)
            failed += 1

    if written:
        logger.info("Automatic backup wrote %d user snapshot(s)", written)
    return {"accounts": len(user_ids), "written": written, "failed": failed}
