"""Operator orchestration for a verified recovery point; no repository secrets enter the app."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

from deployment_backups import bundle
from deployment_backups.compose import Deployment, environment
from deployment_backups.database import verify_database_restore
from deployment_backups.process import BackupError, write_json


def _valid_key(value: str) -> bool:
    try:
        return len(base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)) == 32
    except (ValueError, UnicodeError):
        return False


def captured_key_digest(directory: Path, metadata: dict) -> str:
    """Read bounded key copies directly from verified tar members, without extraction."""
    app_env = metadata["services"]["app"]["environment"]
    values = []
    explicit = app_env.get("SECRET_KEY", "").strip()
    if explicit:
        if not _valid_key(explicit):
            raise BackupError("captured-deployment-key-invalid")
        values.append(explicit)
    key_root = PurePosixPath(app_env.get("APP_DATA_DIR", "/data")) / "config"
    for name in ("fernet.key", "fernet.key.1", "fernet.key.2"):
        key_path = key_root / name
        mounts = [
            item
            for item in metadata["mounts"]
            if item["role"] == "app" and key_path.is_relative_to(item["destination"])
        ]
        if not mounts:
            raise BackupError("deployment-key-must-be-backed-up")
        mount = max(mounts, key=lambda item: len(PurePosixPath(item["destination"]).parts))
        destination = PurePosixPath(mount["destination"])
        member_path = str(PurePosixPath(destination.name) / key_path.relative_to(destination))
        with tarfile.open(directory / mount["archive"], "r|") as archive:
            for member in archive:
                if str(PurePosixPath(member.name)) != member_path:
                    continue
                if not member.isfile() or member.size > 128:
                    raise BackupError("captured-deployment-key-file-invalid")
                stream = archive.extractfile(member)
                assert stream is not None
                try:
                    value = stream.read(128).decode().strip()
                except UnicodeError:
                    break
                if _valid_key(value):
                    values.append(value)
                break
    if not values or len(set(values)) != 1:
        raise BackupError("captured-deployment-key-missing-or-conflicting")
    return hashlib.sha256(values[0].encode()).hexdigest()


class UpgradePreparation:
    def __init__(self, deployment: Deployment, image: str):
        self.deployment = deployment
        self.runner = deployment.runner
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise BackupError("upgrade-requires-immutable-local-image-id")
        inspected = self.runner.json(["docker", "image", "inspect", image], "inspect-upgrade-image")
        if len(inspected) != 1 or inspected[0]["Id"] != image:
            raise BackupError("upgrade-image-identity-mismatch")
        environment(inspected[0])
        self.image = image
        app = deployment.instances["app"]
        database = deployment.instances["database"]
        shared = set(app.info["NetworkSettings"]["Networks"]) & set(
            database.info["NetworkSettings"]["Networks"]
        )
        networks = [
            app.info["NetworkSettings"]["Networks"][name]["NetworkID"]
            for name in sorted(shared)
            if app.info["NetworkSettings"]["Networks"][name]["NetworkID"]
            == database.info["NetworkSettings"]["Networks"][name]["NetworkID"]
        ]
        if not networks:
            raise BackupError("upgrade-probe-requires-shared-database-network")
        self.network = networks[0]
        self.app = app
        source = environment(app.info)
        self.env = {
            key: source[key]
            for key in (
                "DATABASE_URL",
                "POSTGRES_USER",
                "POSTGRES_PASSWORD",
                "POSTGRES_DB",
                "POSTGRES_HOST",
                "POSTGRES_PORT",
                "SECRET_KEY",
                "MIGRATION_BACKUP_MAX_AGE_SECONDS",
            )
            if key in source
        }
        self.env.update(APP_DATA_DIR=source.get("APP_DATA_DIR", "/data"), ENV_FILE="/dev/null")

    def invoke(self, *, marker: Path | None = None) -> bytes:
        arguments = [
            "docker",
            "create",
            "--interactive",
            "--network",
            self.network,
            "--volumes-from",
            self.app.identifier + ":rw",
            "--user",
            self.app.info["Config"].get("User") or "0",
            "--entrypoint",
            "python",
            "--workdir",
            "/app",
        ]
        for key in self.env:
            arguments.extend(["--env", key])  # Values travel in the child environment, never argv.
        arguments.extend([self.image, "-m", "src.database.prepare_upgrade"])
        if marker is None:
            arguments.append("--invalidate")
        identity = (
            self.runner.run(
                arguments, "create-upgrade-issuer", environment=self.env, max_bytes=4096
            )
            .decode()
            .strip()
        )
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise BackupError("invalid-upgrade-issuer-id")
        try:
            return self.runner.run(
                ["docker", "start", "--attach", "--interactive", identity],
                "run-upgrade-issuer",
                input_file=marker,
                max_bytes=16384,
            )
        finally:
            # No --volumes: inherited application volumes must always survive.
            self.runner.run(
                ["docker", "rm", "--force", identity],
                "remove-upgrade-issuer",
                timeout=10,
                max_bytes=4096,
            )

    def verify_and_issue(self, directory: Path, snapshot: str, marker: Path) -> None:
        metadata = bundle.load_json(directory / "deployment.json")
        verify_database_restore(
            self.runner,
            directory / "database.dump",
            metadata["database"],
            metadata["services"]["database"]["image_id"],
        )
        database = metadata["database"]
        verified_at = self.deployment.sql(
            "SELECT floor(extract(epoch FROM clock_timestamp()))::bigint"
        )
        write_json(
            marker,
            {
                "snapshot_id": snapshot,
                "manifest_sha256": bundle.digest(directory / "manifest.json"),
                "key_sha256": captured_key_digest(directory, metadata),
                "source_image_id": metadata["services"]["app"]["image_id"],
                "source_revisions": sorted(database["revisions"]),
                "database": database["database"],
                "user": database["user"],
                "captured_at": database["captured_at"],
                "verified_at": verified_at,
            },
        )
        try:
            result = json.loads(self.invoke(marker=marker))
        except (ValueError, UnicodeError):
            raise BackupError("invalid-upgrade-issuer-result") from None
        if result.get("status") != "recovery-point-issued" or result.get("snapshot_id") != snapshot:
            raise BackupError("upgrade-recovery-point-not-issued")
