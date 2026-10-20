"""Migration policy and private per-attempt diagnostics, under the advisory lock."""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import time
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.database.backup_gate import (
    RecoveryPointError,
    database_fingerprint,
    migration_code_digest,
    read_recovery_point,
    validate_recovery_point,
    write_private_document,
)


def recovery_directory() -> Path:
    return Path(os.getenv("APP_DATA_DIR", "/data")) / "config" / "migration-recovery"


class MigrationSafety:
    """One approval covers this locked attempt, including post-upgrade repair."""

    def __init__(self) -> None:
        from src.core.config import settings  # pylint: disable=import-outside-toplevel

        self.mode = settings.MIGRATION_BACKUP_MODE
        self.maximum_age = settings.MIGRATION_BACKUP_MAX_AGE_SECONDS
        self.secret = settings.SECRET_KEY
        self.path = recovery_directory() / ("attempt-" + uuid4().hex + ".json")
        self.approved = False
        self.report: dict[str, object] = {
            "version": 1,
            "status": "waiting-for-database",
            "mode": self.mode,
            "started_at": int(time()),
            "backup_id": None,
        }
        self.record("waiting-for-database")

    def record(self, status: str, **values: object) -> None:
        self.report.update(values)
        self.report.update(status=status, updated_at=int(time()))
        write_private_document(self.path, json.dumps(self.report, sort_keys=True).encode() + b"\n")

    def approve(self, connection: Connection, current: set[str], target: set[str]) -> None:
        if self.approved:
            return
        if self.mode == "development":
            print("Development opt-out: migration backup verification is disabled.", flush=True)
            self.record("development-opt-out")
        else:
            claims = read_recovery_point(recovery_directory() / "point.json", self.secret)
            now = int(connection.scalar(text("SELECT extract(epoch FROM clock_timestamp())")))
            connection.commit()
            validate_recovery_point(
                claims,
                fingerprint=database_fingerprint(connection.engine.url, self.secret),
                current=current,
                target=target,
                code_digest=migration_code_digest(Path(__file__).parent),
                now=now,
                maximum_age=self.maximum_age,
            )
            self.record(
                "recovery-verified",
                backup_id=claims.snapshot_id,
                captured_at=claims.captured_at,
                verified_at=claims.verified_at,
                source_image_id=claims.source_image_id,
            )
        self.approved = True
        self.record("migrating")

    def failed(self, error: BaseException) -> None:
        # Driver/config/migration exception text can contain secrets and SQL parameters.
        code = str(error) if isinstance(error, RecoveryPointError) else type(error).__name__
        self.record("failed", error=code, finished_at=int(time()))
