"""Capture a configured Compose deployment through the operator's Docker CLI."""

from __future__ import annotations

import hmac
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from deployment_backups.process import BackupError, Runner, write_json

_PSQL = """
export PGPASSWORD="${POSTGRES_PASSWORD:-}"
if [ -n "${POSTGRES_PASSWORD_FILE:-}" ]; then PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"; fi
export PGPASSWORD
exec psql --username="${POSTGRES_USER:-postgres}" --dbname="${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" \
  -XAt --set=ON_ERROR_STOP=1 --command "$1"
"""
_DUMP = """
export PGPASSWORD="${POSTGRES_PASSWORD:-}"
if [ -n "${POSTGRES_PASSWORD_FILE:-}" ]; then PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"; fi
export PGPASSWORD
exec pg_dump --username="${POSTGRES_USER:-postgres}" --dbname="${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" \
  --format=custom --no-owner --no-acl
"""


def environment(info: dict[str, Any]) -> dict[str, str]:
    entries = info.get("Config", {}).get("Env", [])
    result = dict(entry.split("=", 1) for entry in entries if "=" in entry)
    if any(key.startswith("RESTIC_") and value for key, value in result.items()):
        raise BackupError("backup-credentials-must-not-be-in-application-containers")
    return result


@dataclass
class Instance:
    role: str
    service: str
    identifier: str
    info: dict[str, Any]

    @property
    def running(self) -> bool:
        return self.info["State"]["Running"] is True


class Deployment:
    def __init__(
        self,
        runner: Runner,
        project: str,
        files: list[Path],
        *,
        app: str = "app",
        runtime: str = "plugin-runtime",
        database: str = "db",
    ):
        self.runner = runner
        self.project = project
        self.files = [path.resolve(strict=True) for path in files]
        self.services = {"app": app, "runtime": runtime, "database": database}
        self.prefix = ["docker", "compose", "--project-name", project]
        for path in self.files:
            self.prefix.extend(["--file", str(path)])
        self.instances: dict[str, Instance] = {}

    def inspect(self, role: str) -> Instance:
        service = self.services[role]
        identifiers = (
            self.runner.run([*self.prefix, "ps", "--all", "--quiet", service], "inspect-service")
            .decode()
            .split()
        )
        if len(identifiers) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", identifiers[0]):
            raise BackupError("each-backup-service-must-have-exactly-one-container")
        info = self.runner.json(["docker", "inspect", identifiers[0]], "inspect-container")[0]
        labels = info["Config"].get("Labels", {})
        if (
            labels.get("com.docker.compose.project") != self.project
            or labels.get("com.docker.compose.service") != service
        ):
            raise BackupError("container-project-or-service-mismatch")
        configured = labels.get("com.docker.compose.project.config_files", "").split(",")
        if configured != [str(path) for path in self.files]:
            raise BackupError("container-compose-file-mismatch")
        return Instance(role, service, identifiers[0], info)

    def preflight(self) -> dict[str, Any]:
        self.instances = {role: self.inspect(role) for role in self.services}
        for instance in self.instances.values():
            environment(instance.info)
        database = self.instances["database"]
        if not database.running:
            raise BackupError("database-is-not-running")
        self._validate_database()
        metadata = self.sql(
            "SELECT json_build_object('database', current_database(), 'user', current_user, "
            "'server_version_num', current_setting('server_version_num'), "
            "'database_bytes', pg_database_size(current_database()), "
            "'captured_at', floor(extract(epoch FROM clock_timestamp()))::bigint, "
            "'encoding', pg_encoding_to_char(encoding), 'lc_collate', datcollate, "
            "'lc_ctype', datctype) FROM pg_database WHERE datname = current_database()"
        )
        has_revision = self.sql("SELECT to_regclass('alembic_version') IS NOT NULL", as_json=False)
        metadata["revisions"] = (
            self.sql("SELECT coalesce(json_agg(version_num), '[]'::json) FROM alembic_version")
            if has_revision.strip() == "t"
            else []
        )
        mounts = self.mounts()
        for role, root in (
            ("app", "/data"),
            (
                "app",
                environment(self.instances["app"].info).get("APP_DATA_DIR", "/data"),
            ),
            (
                "runtime",
                environment(self.instances["runtime"].info).get(
                    "PLUGIN_ROOT", "/var/lib/unnamed-tracking/plugins"
                ),
            ),
        ):
            if not PurePosixPath(root).is_absolute() or not any(
                item["role"] == role and PurePosixPath(root).is_relative_to(item["destination"])
                for item in mounts
            ):
                raise BackupError("application-and-plugin-persistent-roots-must-be-mounted")
        return {
            "database": metadata,
            "mounts": mounts,
            "writers": {
                instance.service: instance.running
                for role, instance in self.instances.items()
                if role != "database"
            },
        }

    def sql(self, statement: str, *, as_json: bool = True) -> Any:
        raw = self.runner.run(
            [
                "docker",
                "exec",
                self.instances["database"].identifier,
                "sh",
                "-c",
                _PSQL,
                "backup",
                statement,
            ],
            "database-metadata",
        ).decode()
        try:
            return json.loads(raw) if as_json else raw
        except ValueError:
            raise BackupError("invalid-database-metadata") from None

    def _validate_database(self) -> None:
        app = self.instances["app"]
        database = self.instances["database"]
        app_env, db_env = environment(app.info), environment(database.info)
        if all(app_env.get(key) for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")):
            user, password, name = (
                app_env[key] for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
            )
            host, port = (
                app_env.get("POSTGRES_HOST", "db"),
                int(app_env.get("POSTGRES_PORT", "5432")),
            )
        else:
            try:
                url = urlsplit(app_env.get("DATABASE_URL", ""))
                if url.scheme not in {
                    "postgres",
                    "postgresql",
                    "postgresql+psycopg",
                    "postgresql+asyncpg",
                }:
                    raise ValueError
                user, password, name = (
                    unquote(url.username or ""),
                    unquote(url.password or ""),
                    unquote(url.path.lstrip("/")),
                )
                host, port = url.hostname, url.port or 5432
            except ValueError:
                raise BackupError("invalid-app-database-configuration") from None
        aliases = set()
        app_networks = app.info.get("NetworkSettings", {}).get("Networks", {})
        for key, network in database.info.get("NetworkSettings", {}).get("Networks", {}).items():
            if key in app_networks and network.get("NetworkID") == app_networks[key].get(
                "NetworkID"
            ):
                aliases.update(network.get("Aliases") or [])
                aliases.add(network.get("IPAddress"))
        db_user = db_env.get("POSTGRES_USER", "postgres")
        db_password = db_env.get("POSTGRES_PASSWORD", "")
        if db_env.get("POSTGRES_PASSWORD_FILE"):
            db_password = (
                self.runner.run(
                    [
                        "docker",
                        "exec",
                        database.identifier,
                        "cat",
                        db_env["POSTGRES_PASSWORD_FILE"],
                    ],
                    "database-password-file",
                    max_bytes=65536,
                )
                .decode()
                .rstrip("\n")
            )
        if (
            host not in aliases
            or port != int(db_env.get("PGPORT", "5432"))
            or user != db_user
            or name != db_env.get("POSTGRES_DB", db_user)
            or not hmac.compare_digest(password.encode(), db_password.encode())
        ):
            raise BackupError("app-and-backup-database-configuration-mismatch")

    def mounts(self) -> list[dict[str, Any]]:
        results = []
        database = self.instances["database"]
        pgdata = PurePosixPath(environment(database.info).get("PGDATA", "/var/lib/postgresql/data"))
        live_database = {
            mount["Source"]
            for mount in database.info.get("Mounts", [])
            if pgdata.is_relative_to(mount["Destination"])
        }
        for role, instance in self.instances.items():
            for mount in instance.info.get("Mounts", []):
                if mount["Type"] not in {"bind", "volume"}:
                    continue
                destination = PurePosixPath(mount["Destination"])
                if not destination.is_absolute() or ".." in destination.parts:
                    raise BackupError("invalid-container-mount-path")
                if role == "database" and pgdata.is_relative_to(destination):
                    continue  # Never copy the live PostgreSQL data volume.
                if any(
                    PurePosixPath(mount["Source"]).is_relative_to(source)
                    or PurePosixPath(source).is_relative_to(mount["Source"])
                    for source in live_database
                ):
                    raise BackupError("live-database-volume-shared-with-another-service")
                results.append(
                    {
                        "role": role,
                        "destination": str(destination),
                        "source": mount["Source"],
                        "type": mount["Type"],
                        "read_only": not mount.get("RW", False),
                        "archive": f"mount-{len(results):04}.tar",
                    }
                )
        return results

    def stop_writers(self) -> None:
        self.runner.run(
            [
                *self.prefix,
                "stop",
                "--timeout",
                "30",
                self.services["app"],
                self.services["runtime"],
            ],
            "stop-writers",
        )
        if self.inspect("app").running or self.inspect("runtime").running:
            raise BackupError("writers-did-not-stop")

    def resume_writers(self) -> None:
        previous = [
            instance.service
            for role, instance in self.instances.items()
            if role != "database" and instance.running
        ]
        if not previous:
            return
        self.runner.run([*self.prefix, "start", *previous], "resume-writers")
        deadline = time.monotonic() + min(self.runner.timeout, 180)
        pending = [role for role in ("app", "runtime") if self.instances[role].running]
        while pending:
            remaining = []
            for role in pending:
                current = self.inspect(role)
                health = current.info["State"].get("Health", {}).get("Status", "healthy")
                if not current.running or health != "healthy":
                    remaining.append(role)
            pending = remaining
            if pending:
                if time.monotonic() >= deadline:
                    raise BackupError("resumed-services-did-not-become-healthy")
                time.sleep(1)

    def capture(self, directory: Path, preflight: dict[str, Any], max_bytes: int) -> None:
        other_clients = self.sql(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND backend_type = 'client backend' AND pid <> pg_backend_pid()",
            as_json=False,
        )
        if other_clients.strip() != "0":
            raise BackupError("database-still-has-other-clients-after-maintenance-stop")
        remaining = max_bytes
        dump = directory / "database.dump"
        self.runner.run(
            [
                "docker",
                "exec",
                self.instances["database"].identifier,
                "sh",
                "-c",
                _DUMP,
            ],
            "database-dump",
            output_file=dump,
            max_bytes=remaining,
        )
        remaining -= dump.stat().st_size
        for mount in preflight["mounts"]:
            target = directory / mount["archive"]
            self.runner.run(
                [
                    "docker",
                    "cp",
                    "-a",
                    self.instances[mount["role"]].identifier + ":" + mount["destination"],
                    "-",
                ],
                "capture-persistent-mount",
                output_file=target,
                max_bytes=remaining,
            )
            remaining -= target.stat().st_size
        write_json(
            directory / "deployment.json",
            {
                "project": self.project,
                "database": preflight["database"],
                "mounts": preflight["mounts"],
                "services": {
                    role: {
                        "service": instance.service,
                        "image_id": instance.info["Image"],
                        "configured_image": instance.info["Config"]["Image"],
                        "environment": environment(instance.info),
                        "was_running": instance.running,
                    }
                    for role, instance in self.instances.items()
                },
            },
        )
        for index, path in enumerate(self.files):
            target = directory / f"compose-{index:04}.yaml"
            with target.open("xb") as output:
                target.chmod(0o600)
                output.write(path.read_bytes())

    def validate_dump(self, dump: Path) -> None:
        self.runner.run(
            [
                "docker",
                "exec",
                "-i",
                self.instances["database"].identifier,
                "pg_restore",
                "--list",
            ],
            "database-dump-validation",
            input_file=dump,
            max_bytes=64 * 1024 * 1024,
        )
