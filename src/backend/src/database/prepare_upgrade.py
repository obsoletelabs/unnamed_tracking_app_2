"""One-shot operator issuer, invoked only after full backup and isolated restore verification."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import inspect, text

from src.database.backup_gate import (
    MAX_PROOF_BYTES,
    RecoveryClaims,
    RecoveryPointError,
    database_fingerprint,
    migration_code_digest,
    validate_recovery_point,
    write_recovery_point,
)
from src.database.migration_safety import recovery_directory


class VerifiedCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_revisions: list[str] = Field(max_length=32)
    database: str = Field(min_length=1, max_length=128)
    user: str = Field(min_length=1, max_length=128)
    captured_at: int = Field(ge=0)
    verified_at: int = Field(ge=0)


def existing_key_digest() -> str:
    """Do not import Settings until an existing key is matched to the restored capture."""
    values = []
    explicit = os.getenv("SECRET_KEY", "").strip()
    if explicit:
        Fernet(explicit.encode())
        values.append(explicit)
    root = Path(os.getenv("APP_DATA_DIR", "/data")) / "config"
    for name in ("fernet.key", "fernet.key.1", "fernet.key.2"):
        try:
            descriptor = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RecoveryPointError("existing-deployment-key-file-invalid")
            try:
                value = stream.read(128).decode().strip()
                Fernet(value.encode())
            except (ValueError, UnicodeError):
                continue  # A damaged redundant copy is handled by the existing key policy.
            values.append(value)
    if not values or len(set(values)) != 1:
        raise RecoveryPointError("existing-deployment-key-required-and-must-match")
    return hashlib.sha256(values[0].encode()).hexdigest()


def issue(capture: VerifiedCapture) -> None:
    if not hmac.compare_digest(existing_key_digest(), capture.key_sha256):
        raise RecoveryPointError("restored-deployment-key-mismatch")
    # Importing settings may repair redundant key copies, but cannot generate a new
    # key: the original matching key has already been checked above.
    from src.core.config import settings  # pylint: disable=import-outside-toplevel
    from src.database.migrate import _engine, _migration_lock, _wait_for_database  # pylint: disable=import-outside-toplevel

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    engine = _engine()
    try:
        _wait_for_database(engine)
        with _migration_lock(engine) as connection:
            identity = connection.execute(
                text(
                    "SELECT current_database(), current_user, floor(extract(epoch FROM clock_timestamp()))::bigint"
                )
            ).one()
            if (identity[0], identity[1]) != (capture.database, capture.user):
                raise RecoveryPointError("restored-database-identity-mismatch")
            tables = inspect(connection).get_table_names()
            current = (
                {
                    row[0]
                    for row in connection.execute(text("SELECT version_num FROM alembic_version"))
                }
                if "alembic_version" in tables
                else set()
            )
            connection.commit()
            claims = RecoveryClaims(
                version=1,
                database_fingerprint=database_fingerprint(engine.url, settings.SECRET_KEY),
                source_revisions=sorted(capture.source_revisions),
                target_revisions=sorted(script.get_heads()),
                target_code_sha256=migration_code_digest(Path(__file__).parent),
                snapshot_id=capture.snapshot_id,
                manifest_sha256=capture.manifest_sha256,
                source_image_id=capture.source_image_id,
                captured_at=capture.captured_at,
                verified_at=capture.verified_at,
                verification="isolated-postgresql-restore-v1",
            )
            validate_recovery_point(
                claims,
                fingerprint=claims.database_fingerprint,
                current=current,
                target=set(script.get_heads()),
                code_digest=claims.target_code_sha256,
                now=int(identity[2]),
                maximum_age=settings.MIGRATION_BACKUP_MAX_AGE_SECONDS,
            )
            write_recovery_point(recovery_directory() / "point.json", claims, settings.SECRET_KEY)
            print(
                json.dumps(
                    {
                        "status": "recovery-point-issued",
                        "snapshot_id": claims.snapshot_id,
                        "target_revisions": claims.target_revisions,
                    }
                ),
                flush=True,
            )
    finally:
        engine.dispose()


def main() -> int:
    try:
        if sys.argv[1:] == ["--invalidate"]:
            directory = recovery_directory()
            if directory.is_symlink() or (
                directory.exists()
                and (
                    directory.is_symlink()
                    or directory.stat().st_uid != os.getuid()
                    or directory.stat().st_mode & 0o077
                )
            ):
                raise RecoveryPointError("recovery-point-directory-permissions-invalid")
            (directory / "point.json").unlink(missing_ok=True)
            if directory.exists():
                descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return 0
        if sys.argv[1:]:
            raise RecoveryPointError("invalid-issuer-options")
        raw = sys.stdin.buffer.read(MAX_PROOF_BYTES + 1)
        if len(raw) > MAX_PROOF_BYTES:
            raise RecoveryPointError("verified-capture-size-invalid")
        issue(VerifiedCapture.model_validate_json(raw))
        return 0
    except Exception as error:  # pylint: disable=broad-exception-caught
        # Do not disclose source environment, JSON validation input or SQL errors.
        detail = str(error) if isinstance(error, RecoveryPointError) else type(error).__name__
        print("Recovery preparation failed: " + detail, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
