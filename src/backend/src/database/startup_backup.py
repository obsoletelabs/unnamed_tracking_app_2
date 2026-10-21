"""Automatic local recovery before a recognized upgrade, without Docker privileges.

This is deliberately separate from the operator's full-deployment Restic workflow:
it captures PostgreSQL and APP_DATA_DIR while this app is stopped, not external
runtime volumes or container images. Verification reads the snapshot back and
checks the PostgreSQL archive; it never restores into the running database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import time
from uuid import uuid4

from sqlalchemy.engine import URL

from src.core.backup_process import BackupError, Runner, private_directory, write_json

PENDING = "uta-startup-pending-v1"
COMPLETE = "uta-startup-complete-v1"
MAX_DUMP_BYTES = 100 * 1024**3
KEEP_LAST = 7


@dataclass(frozen=True)
class StartupRecovery:
    """Only a read-back verified snapshot can authorize this migration attempt."""

    snapshot_id: str
    captured_at: int
    verified_at: int
    retention_warning: bool = False


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _exclude(path: Path) -> str:
    # Restic exclusions are patterns, even for absolute paths supplied as argv.
    return str(path).replace("[", "[[]").replace("*", "[*]").replace("?", "[?]")


# libpq reads these from the environment, which keeps credentials out of argv.
_LIBPQ_OPTIONS = {
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslcrl": "PGSSLCRL",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


def _libpq_environment(url: URL) -> dict[str, str]:
    values = {
        "PGHOST": url.host,
        "PGPORT": None if url.port is None else str(url.port),
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
    }
    for key, value in url.query.items():
        # Fail closed instead of silently dumping a different server than the app uses.
        if key not in _LIBPQ_OPTIONS or not isinstance(value, str):
            raise BackupError("startup-backup-unsupported-database-url-option")
        values[_LIBPQ_OPTIONS[key]] = value
    return {key: value for key, value in values.items() if value}


class StartupBackup:
    """An app-owned, encrypted local repository with no operator credentials."""

    def __init__(self, data: Path, recovery: Path, *, runner: Runner | None = None):
        self.data = data.resolve()
        if self.data == Path(self.data.anchor):
            raise BackupError("startup-backup-needs-dedicated-app-data-directory")
        self.recovery = private_directory(recovery)
        self.repository = self.data / "backups" / "startup-recovery"
        self.password_file = self.recovery / "local-restic-password"
        self.runner = runner or Runner(1800)
        # Do not let inherited Restic/proxy/password-command variables change the
        # destination or execute an operator's unrelated credential helper.
        self.environment = {"PATH": os.defpath, "LANG": "C.UTF-8"}
        self.prefix = [
            "restic",
            "--json",
            "--no-cache",
            "--repo",
            str(self.repository),
            "--password-file",
            str(self.password_file),
        ]

    def _run(self, arguments: list[str], step: str) -> bytes:
        return self.runner.run(
            arguments, step, environment=self.environment, inherit_environment=False
        )

    def _json(self, arguments: list[str], step: str):
        return self.runner.json(
            arguments, step, environment=self.environment, inherit_environment=False
        )

    def prepare_repository(self) -> None:
        """Create or validate local recovery storage without touching PostgreSQL."""
        for parent in (self.data / "backups", self.repository):
            if parent.is_symlink():
                raise BackupError("startup-backup-repository-path-is-symlink")
        initialized = (self.repository / "config").exists()
        private_directory(self.repository)
        if not initialized and any(self.repository.iterdir()):
            raise BackupError("startup-backup-repository-incomplete")
        if not self.password_file.exists():
            if initialized:
                raise BackupError("startup-backup-restore-missing-local-restic-password")
            descriptor = os.open(
                self.password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(secrets.token_urlsafe(48) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        descriptor = os.open(self.password_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or re.fullmatch(rb"[A-Za-z0-9_-]{64}\n", stream.read(1024)) is None
            ):
                raise BackupError("startup-backup-password-file-unsafe")
        if not initialized:
            self._run([*self.prefix, "init"], "initialize-startup-backup-repository")
        self._json([*self.prefix, "cat", "config"], "check-startup-backup-repository")

    def _snapshot(self, hostname: str, run_tag: str, state: str) -> dict:
        result = self._json(
            [*self.prefix, "snapshots", "--host", hostname, "--tag", state + "," + run_tag],
            "find-startup-backup-snapshot",
        )
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
            raise BackupError("startup-backup-snapshot-mismatch")
        snapshot = result[0]
        if (
            not re.fullmatch(r"[0-9a-f]{64}", snapshot.get("id", ""))
            or snapshot.get("hostname") != hostname
            or not {state, run_tag}.issubset(snapshot.get("tags", []))
        ):
            raise BackupError("startup-backup-snapshot-mismatch")
        return snapshot

    def _dump(self, url: URL, dump: Path, maximum: int) -> None:
        if url.get_backend_name() != "postgresql":
            raise BackupError("startup-backup-requires-postgresql")
        environment = {**self.environment, "PGCONNECT_TIMEOUT": "5", **_libpq_environment(url)}
        # Credentials stay out of argv, diagnostics and the stored manifest.
        self.runner.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--lock-wait-timeout=60s",
            ],
            "capture-startup-database",
            output_file=dump,
            max_bytes=maximum,
            environment=environment,
            inherit_environment=False,
        )

    def capture(self, url: URL, *, secret: str, claims: dict) -> StartupRecovery:
        """Capture, read back and promote one snapshot before allowing schema changes.

        ``claims`` binds the snapshot to the database fingerprint, source/target
        revisions and target migration code, and is stored in the manifest.
        """
        try:
            self.prepare_repository()
            hostname = "uta-startup-" + claims["database_fingerprint"][:32]
            run_tag = "uta-run:" + uuid4().hex
            with tempfile.TemporaryDirectory(prefix="uta-startup-recovery-") as temporary:
                work = Path(temporary)
                bundle = private_directory(work / "bundle")
                captured_at = int(time())
                manifest = self._write_bundle(
                    url, bundle, secret=secret, captured_at=captured_at, claims=claims
                )
                snapshot = self._store(bundle, hostname, run_tag)
                self._read_back(
                    work=work,
                    snapshot_id=snapshot["id"],
                    bundle=bundle,
                    manifest=manifest,
                    secret=secret,
                )
                self._run(
                    [*self.prefix, "tag", "--remove", PENDING, "--add", COMPLETE, snapshot["id"]],
                    "promote-startup-backup",
                )
                complete = self._snapshot(hostname, run_tag, COMPLETE)
                return StartupRecovery(
                    complete["id"], captured_at, int(time()), self._retain(hostname)
                )
        except (OSError, ValueError, TypeError):
            # Paths, conninfo and archive values can contain private data.
            raise BackupError("startup-backup-local-storage-or-configuration-error") from None

    def _write_bundle(
        self, url: URL, bundle: Path, *, secret: str, captured_at: int, claims: dict
    ) -> dict:
        maximum = min(MAX_DUMP_BYTES, shutil.disk_usage(bundle).free // 3)
        if maximum < 16 * 1024**2:
            raise BackupError("startup-backup-insufficient-scratch-space")
        dump = bundle / "database.dump"
        self._dump(url, dump, maximum)
        manifest = {
            "version": 1,
            "kind": "app-local-startup-recovery-v1",
            **claims,
            "captured_at": captured_at,
            "database_sha256": _digest(dump),
        }
        write_json(bundle / "manifest.json", manifest)
        # Keep the effective key even when Portainer supplies it through ENV.
        write_json(bundle / "recovery-keys.json", {"SECRET_KEY": secret})
        return manifest

    def _store(self, bundle: Path, hostname: str, run_tag: str) -> dict:
        self._run(
            [
                *self.prefix,
                "backup",
                "--quiet",
                "--host",
                hostname,
                "--tag",
                PENDING + "," + run_tag,
                "--exclude",
                _exclude(self.repository),
                "--exclude",
                _exclude(self.recovery),
                "--exclude",
                _exclude(self.data / "cache"),
                "--",
                str(bundle),
                str(self.data),
            ],
            "store-startup-backup",
        )
        return self._snapshot(hostname, run_tag, PENDING)

    def _read_back(
        self, *, work: Path, snapshot_id: str, bundle: Path, manifest: dict, secret: str
    ) -> None:
        verified = private_directory(work / "verified")
        self._run(
            [*self.prefix, "restore", snapshot_id, "--target", str(verified), "--verify"],
            "read-back-startup-backup",
        )
        restored = verified / str(bundle).lstrip("/")
        if not restored.resolve().is_relative_to(verified.resolve()):
            raise BackupError("startup-backup-restored-path-invalid")
        if (
            json.loads((restored / "manifest.json").read_bytes()) != manifest
            or json.loads((restored / "recovery-keys.json").read_bytes()) != {"SECRET_KEY": secret}
            or _digest(restored / "database.dump") != manifest["database_sha256"]
        ):
            raise BackupError("startup-backup-readback-mismatch")
        self._run(
            ["pg_restore", "--list", str(restored / "database.dump")],
            "check-startup-postgresql-archive",
        )

    def _retain(self, hostname: str) -> bool:
        """Return True when optional pruning failed; the new point remains usable."""
        try:
            self._run(
                [
                    *self.prefix,
                    "forget",
                    "--host",
                    hostname,
                    "--tag",
                    COMPLETE,
                    "--group-by",
                    "host",
                    "--keep-last",
                    str(KEEP_LAST),
                    "--prune",
                ],
                "retain-startup-backups",
            )
        except BackupError:
            return True
        return False
