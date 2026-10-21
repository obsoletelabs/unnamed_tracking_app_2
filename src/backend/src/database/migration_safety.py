"""Migration policy and private per-attempt diagnostics, under the advisory lock."""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import time
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.core.backup_process import BackupError
from src.database.backup_gate import (
    RecoveryPointError,
    database_fingerprint,
    migration_code_digest,
    read_recovery_point,
    validate_recovery_point,
    write_private_document,
)
from src.database.startup_backup import StartupBackup


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
        elif self.mode == "automatic":
            self._approve_automatically(connection, current, target)
        else:
            self._approve_with_operator_receipt(connection, current, target)
        self.approved = True
        self.record("migrating")

    def _approve_with_operator_receipt(
        self, connection: Connection, current: set[str], target: set[str]
    ) -> None:
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

    def _approve_automatically(
        self, connection: Connection, current: set[str], target: set[str]
    ) -> None:
        """Prefer a valid operator full-deployment point; otherwise capture local recovery."""
        try:
            self._approve_with_operator_receipt(connection, current, target)
            return
        except RecoveryPointError:
            pass
        print("Creating and verifying automatic local recovery before the upgrade.", flush=True)
        self.record("creating-local-recovery")
        try:
            snapshot = StartupBackup(recovery_directory().parents[1], recovery_directory()).capture(
                connection.engine.url,
                secret=self.secret,
                claims={
                    "database_fingerprint": database_fingerprint(
                        connection.engine.url, self.secret
                    ),
                    "source_revisions": sorted(current),
                    "target_revisions": sorted(target),
                    "target_code_sha256": migration_code_digest(Path(__file__).parent),
                },
            )
        except (BackupError, OSError) as error:
            code = str(error) if isinstance(error, BackupError) else "local-storage-error"
            raise RecoveryPointError("automatic-startup-backup-failed:" + code) from None
        self.record(
            "local-recovery-verified",
            backup_id=snapshot.snapshot_id,
            captured_at=snapshot.captured_at,
            verified_at=snapshot.verified_at,
            verification="restic-readback-and-postgresql-archive-v1",
            retention_warning=snapshot.retention_warning,
        )
        print("Automatic local recovery verified; starting database migrations.", flush=True)

    def failed(self, error: BaseException) -> None:
        # Driver/config/migration exception text can contain secrets and SQL parameters.
        code = str(error) if isinstance(error, RecoveryPointError) else type(error).__name__
        self.record("failed", error=code, finished_at=int(time()))
