#!/usr/bin/env python3
"""Operator-only Linux/WSL deployment recovery CLI; see the deployment backup runbook."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from deployment_backups import bundle
from deployment_backups.compose import Deployment
from deployment_backups.process import (
    BackupError,
    Runner,
    deployment_lock,
    private_directory,
    write_json,
)
from deployment_backups.repository import Repository
from deployment_backups.upgrade import UpgradePreparation


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command", choices=("plan", "backup", "prepare-upgrade", "restore", "verify", "retention")
    )
    result.add_argument(
        "--deployment-id", required=True, help="Stable identifier across host replacement"
    )
    result.add_argument("--project", help="Exact Compose project for plan/backup/prepare-upgrade")
    result.add_argument(
        "--upgrade-image", help="Immutable locally available sha256 image ID for prepare-upgrade"
    )
    result.add_argument("--compose-file", type=Path, action="append", default=[])
    result.add_argument("--app-service", default="app")
    result.add_argument("--runtime-service", default="plugin-runtime")
    result.add_argument("--database-service", default="db")
    result.add_argument("--state-dir", type=Path, default=Path("/var/lib/uta-backups"))
    result.add_argument("--staging-dir", type=Path, help="Private disk, outside application mounts")
    result.add_argument(
        "--max-bytes", type=int, default=100 * 1024**3, help="Maximum uncompressed bundle bytes"
    )
    result.add_argument("--timeout", type=int, default=1800, help="Per-command deadline in seconds")
    result.add_argument(
        "--rate-kib", type=int, default=10240, help="Restic transfer limit in KiB/s"
    )
    result.add_argument("--snapshot", default="latest", help="Complete snapshot ID or latest")
    result.add_argument(
        "--target", type=Path, help="New private directory for restore, bundle directory for verify"
    )
    result.add_argument(
        "--apply", action="store_true", help="Apply retention (otherwise preview only)"
    )
    result.add_argument("--keep-last", type=int, default=3)
    result.add_argument("--keep-daily", type=int, default=7)
    result.add_argument("--keep-weekly", type=int, default=4)
    result.add_argument("--keep-monthly", type=int, default=12)
    return result


def check_paths(deployment: Deployment, metadata: dict, protected: list[Path]) -> None:
    for mount in metadata["mounts"]:
        if mount["type"] != "bind":
            continue
        source = mount["source"]
        # Docker Desktop exposes Windows binds under this alternate prefix.
        if source.startswith("/run/desktop/mnt/host/"):
            source = "/mnt/" + source.removeprefix("/run/desktop/mnt/host/")
        source_path = Path(source).resolve()
        for path in protected:
            if path.resolve().is_relative_to(source_path):
                raise BackupError("backup-state-or-credentials-inside-application-mount")
    # Record all supplied configuration files, but reject enormous generated inputs.
    if any(path.stat().st_size > 4 * 1024**2 for path in deployment.files):
        raise BackupError("compose-file-size-limit")


def backup(
    deployment: Deployment, repository: Repository, stage: Path, options, report: dict
) -> None:
    metadata = deployment.preflight()
    protected = [options.state_dir, stage, Path(os.environ["RESTIC_PASSWORD_FILE"])]
    location = os.environ.get("RESTIC_REPOSITORY", "")
    if location.startswith("/"):
        protected.append(Path(location))
    check_paths(deployment, metadata, protected)
    if metadata["database"]["database_bytes"] > options.max_bytes:
        raise BackupError("database-exceeds-backup-budget")
    if shutil.disk_usage(stage).free < options.max_bytes * 2:
        raise BackupError("insufficient-staging-space-for-capture-and-verification")
    preparation = (
        UpgradePreparation(deployment, options.upgrade_image or "")
        if getattr(options, "command", "backup") == "prepare-upgrade"
        else None
    )
    report["writers_resumed"] = False
    report["writers_stopped"] = False
    maintenance_file = options.state_dir / (options.deployment_id + "-maintenance.json")
    maintenance = {
        "run_id": report.get("run_id"),
        "phase": "stopping",
        "previous_writers": metadata.get("writers", {}),
    }
    with tempfile.TemporaryDirectory(prefix="capture-", dir=stage) as temporary:
        directory = private_directory(Path(temporary) / "bundle")
        resume_error = None
        write_json(maintenance_file, maintenance)
        try:
            # Attempt restart even when stop fails part way through.
            deployment.stop_writers()
            report["writers_stopped"] = True
            if preparation is not None:
                preparation.invoke()  # Invalidate any older approval before starting a new attempt.
            deployment.capture(directory, metadata, options.max_bytes)
        finally:
            try:
                if preparation is None:
                    deployment.resume_writers()
                    report["writers_resumed"] = True
            except BackupError as error:
                resume_error = error
                report["resume_error"] = str(error)
            maintenance["phase"] = (
                (
                    "stopped-for-upgrade"
                    if report["writers_stopped"]
                    else "stop-failed-check-writers"
                )
                if preparation is not None
                else ("resumed" if report["writers_resumed"] else "resume-failed")
            )
            maintenance["updated_at"] = datetime.now(UTC).isoformat()
            write_json(maintenance_file, maintenance)
        # Ordinary backups resume before slow verification. Upgrade preparation holds
        # the same maintenance window until approval, leaving writers stopped afterward.
        deployment.validate_dump(directory / "database.dump")
        bundle.seal(directory, options.deployment_id, report["run_id"], options.max_bytes)
        report["snapshot_id"] = repository.store(
            directory, report["run_id"], Path(temporary) / "verification", options.max_bytes
        )
        report["backup_complete"] = True
        if preparation is not None:
            restored = Path(temporary) / "verification" / str(directory.resolve()).lstrip("/")
            preparation.verify_and_issue(
                restored, report["snapshot_id"], Path(temporary) / "verified-capture.json"
            )
            report["upgrade_recovery_verified"] = True
            report["upgrade_image_id"] = preparation.image
            maintenance["phase"] = "ready-for-upgrade"
            maintenance["snapshot_id"] = report["snapshot_id"]
            maintenance["updated_at"] = datetime.now(UTC).isoformat()
            write_json(maintenance_file, maintenance)
        if resume_error is not None:
            raise resume_error


def execute(options, report: dict) -> None:
    runner = Runner(options.timeout)
    if options.command == "verify":
        if options.target is None:
            raise BackupError("verify-requires-bundle-target")
        bundle.verify(options.target.resolve(strict=True), options.deployment_id, options.max_bytes)
        report["bundle_verified"] = True
        return
    repository = Repository(runner, options.deployment_id, rate_kib=options.rate_kib)
    repository.preflight()
    if options.command in {"backup", "plan", "prepare-upgrade"}:
        if not options.project or not options.compose_file:
            raise BackupError("backup-requires-explicit-project-and-compose-files")
        for service in (
            options.project,
            options.app_service,
            options.runtime_service,
            options.database_service,
        ):
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,100}", service):
                raise BackupError("invalid-compose-project-or-service")
        if len({options.app_service, options.runtime_service, options.database_service}) != 3:
            raise BackupError("backup-services-must-be-distinct")
        deployment = Deployment(
            runner,
            options.project,
            options.compose_file,
            app=options.app_service,
            runtime=options.runtime_service,
            database=options.database_service,
        )
        if options.command == "plan":
            metadata = deployment.preflight()
            report.update(
                {
                    "database_bytes": metadata["database"]["database_bytes"],
                    "persistent_mounts": len(metadata["mounts"]),
                    "maintenance_required": True,
                }
            )
            return
        stage = private_directory(options.staging_dir or options.state_dir / "staging")
        backup(deployment, repository, stage, options, report)
    elif options.command == "restore":
        if options.target is None:
            raise BackupError("restore-requires-new-target")
        snapshot = repository.select(options.snapshot)
        repository.restore(snapshot, options.target.absolute(), options.max_bytes)
        report.update({"snapshot_id": snapshot["id"], "bundle_verified": True})
    elif options.command == "retention":
        repository.retain(
            last=options.keep_last,
            daily=options.keep_daily,
            weekly=options.keep_weekly,
            monthly=options.keep_monthly,
            dry_run=not options.apply,
        )
        report["retention_applied"] = options.apply


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    if sys.platform != "linux":
        print('{"status":"failed","error":"run-on-linux-or-wsl"}')
        return 1
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,100}", options.deployment_id)
        or options.max_bytes < 1024**2
        or options.timeout < 1
        or options.rate_kib < 1
    ):
        print('{"status":"failed","error":"invalid-backup-options"}')
        return 1
    os.umask(0o077)
    report = {
        "command": options.command,
        "run_id": uuid4().hex,
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "backup_complete": False,
    }

    def interrupted(_signum, _frame):
        raise BackupError("interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        options.state_dir = private_directory(options.state_dir)
        with deployment_lock(options.state_dir, options.deployment_id):
            status_file = options.state_dir / (
                options.deployment_id + "-" + options.command + ".json"
            )
            if status_file.exists():
                previous = bundle.load_json(status_file)
                report["last_success_at"] = previous.get("last_success_at")
            # A killed process must not leave an old success looking like the current run.
            write_json(status_file, report)
            try:
                execute(options, report)
                report["status"] = "success"
                if options.command in {"backup", "prepare-upgrade"}:
                    report["last_success_at"] = datetime.now(UTC).isoformat()
            except BackupError as error:
                report.update({"status": "failed", "error": str(error)})
            except (OSError, ValueError, TypeError, KeyError, IndexError):
                report.update(
                    {"status": "failed", "error": "invalid-configuration-or-recovery-data"}
                )
            report["finished_at"] = datetime.now(UTC).isoformat()
            write_json(status_file, report)
    except BackupError as error:
        report.update({"status": "failed", "error": str(error)})
    except (OSError, ValueError):
        report.update({"status": "failed", "error": "cannot-acquire-private-operator-state"})
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
