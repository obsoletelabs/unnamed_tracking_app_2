"""Restore logical backups in an owned, offline PostgreSQL probe before issuing evidence."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from deployment_backups.process import BackupError, Runner


def _connection(user: str, database: str) -> list[str]:
    return ["--username=" + user, "--dbname=postgresql:///" + database]


def verify_database_restore(
    runner: Runner, dump: Path, metadata: dict[str, Any], database_image: str
) -> None:
    """Never attach source volumes or networks; remove only the newly created probe ID."""
    user = metadata.get("user")
    size = metadata.get("database_bytes")
    revisions = metadata.get("revisions")
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", database_image)
        or not isinstance(user, str)
        or not user
        or len(user.encode()) > 128
        or "\x00" in user
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or not isinstance(revisions, list)
        or len(revisions) > 32
        or any(not isinstance(value, str) for value in revisions)
    ):
        raise BackupError("invalid-database-restore-metadata")
    identity = None
    probe_database = "uta_backup_verify"
    try:
        created = (
            runner.run(
                [
                    "docker",
                    "create",
                    "--network",
                    "none",
                    "--label",
                    "io.obsoletelabs.backup-probe=" + uuid4().hex,
                    "--env",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    "--env",
                    "POSTGRES_USER=" + user,
                    "--env",
                    "POSTGRES_DB=" + probe_database,
                    "--env",
                    "PGPORT=5432",
                    "--env",
                    "PGHOST=/var/run/postgresql",
                    "--volume",
                    "/var/lib/postgresql",
                    database_image,
                ],
                "create-database-restore-probe",
                max_bytes=4096,
            )
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[0-9a-f]{64}", created):
            raise BackupError("invalid-database-restore-probe-id")
        identity = created
        runner.run(
            ["docker", "start", identity],
            "start-database-restore-probe",
            max_bytes=4096,
        )
        _wait_ready(runner, identity, user, probe_database)
        free = runner.run(
            ["docker", "exec", identity, "df", "-Pk", "/var/lib/postgresql"],
            "database-restore-space",
            max_bytes=4096,
        ).decode()
        try:
            available = int(free.splitlines()[1].split()[3]) * 1024
        except (ValueError, IndexError):
            raise BackupError("invalid-database-restore-space") from None
        if available < size * 2 + 128 * 1024**2:
            raise BackupError("insufficient-space-for-database-restore")
        # Restore into a fresh template0 database, matching the source encoding/locales
        # when available. This probe never receives live database credentials.
        target = "uta_restore_" + uuid4().hex[:20]
        create = [
            "docker",
            "exec",
            identity,
            "createdb",
            "--username=" + user,
            "--template=template0",
        ]
        for field, option in (
            ("encoding", "--encoding="),
            ("lc_collate", "--lc-collate="),
            ("lc_ctype", "--lc-ctype="),
        ):
            if field in metadata:
                if (
                    not isinstance(metadata[field], str)
                    or not metadata[field]
                    or "\x00" in metadata[field]
                ):
                    raise BackupError("invalid-database-restore-locale")
                create.append(option + metadata[field])
        runner.run([*create, "--", target], "create-restoration-database", max_bytes=4096)
        runner.run(
            [
                "docker",
                "exec",
                "-i",
                identity,
                "pg_restore",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
                *_connection(user, target),
            ],
            "restore-database-archive",
            input_file=dump,
            max_bytes=65536,
        )
        psql = [
            "docker",
            "exec",
            identity,
            "psql",
            "-X",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            *_connection(user, target),
        ]
        has_versions = (
            runner.run(
                [*psql, "-c", "SELECT to_regclass('alembic_version') IS NOT NULL"],
                "inspect-restored-history",
                max_bytes=4096,
            )
            .decode()
            .strip()
        )
        if has_versions not in {"t", "f"}:
            raise BackupError("invalid-restored-history-result")
        restored = (
            runner.json(
                [
                    *psql,
                    "-c",
                    "SELECT coalesce(json_agg(version_num ORDER BY version_num), '[]'::json) FROM alembic_version",
                ],
                "read-restored-history",
                max_bytes=16384,
            )
            if has_versions == "t"
            else []
        )
        if restored != sorted(revisions):
            raise BackupError("restored-database-revision-mismatch")
    finally:
        if identity is not None:
            # An ID is recorded only after a successful create. No name or existing
            # deployment is ever removed if creation fails or returns invalid output.
            runner.run(
                ["docker", "rm", "--force", "--volumes", identity],
                "remove-database-restore-probe",
                timeout=10,
                max_bytes=4096,
            )


def _wait_ready(runner: Runner, identity: str, user: str, database: str) -> None:
    deadline = time.monotonic() + min(runner.timeout, 120)
    while True:
        try:
            # Official PostgreSQL initialization briefly accepts connections on a
            # temporary server. Wait until PID 1 is the final database process.
            process = (
                runner.run(
                    ["docker", "exec", identity, "cat", "/proc/1/comm"],
                    "inspect-database-restore-process",
                    timeout=5,
                    max_bytes=4096,
                )
                .decode()
                .strip()
            )
            if process != "postgres":
                raise BackupError("database-restore-probe-initializing")
            runner.run(
                [
                    "docker",
                    "exec",
                    identity,
                    "pg_isready",
                    "--timeout=2",
                    *_connection(user, database),
                ],
                "wait-database-restore-probe",
                timeout=5,
                max_bytes=4096,
            )
            return
        except BackupError:
            state = runner.json(
                ["docker", "inspect", "--format", "{{json .State}}", identity],
                "inspect-database-restore-probe",
                timeout=5,
                max_bytes=4096,
            )
            if (
                not isinstance(state, dict)
                or not state.get("Running")
                or time.monotonic() >= deadline
            ):
                raise BackupError("database-restore-probe-not-ready") from None
            time.sleep(0.25)
