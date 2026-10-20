"""Restic transport. Only fully restored and checked bundles receive the complete tag."""

from __future__ import annotations

import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from deployment_backups import bundle
from deployment_backups.process import BackupError, Runner, private_directory

COMPLETE = "uta-complete-v1"
PENDING = "uta-pending-v1"


class Repository:
    def __init__(self, runner: Runner, deployment_id: str, *, rate_kib: int = 10240):
        self.runner = runner
        self.deployment_id = deployment_id
        self.tag = "uta-deployment:" + deployment_id
        self.prefix = [
            "restic",
            "--json",
            "--no-cache",
            "--limit-upload",
            str(rate_kib),
            "--limit-download",
            str(rate_kib),
        ]

    def preflight(self) -> None:
        repository = os.environ.get("RESTIC_REPOSITORY", "")
        # Do not permit plain HTTP or arbitrary restic shell backends in this wrapper.
        if not repository or not (
            repository.startswith(("/", "sftp:", "s3:https://", "rest:https://"))
        ):
            raise BackupError("use-local-sftp-or-https-restic-repository")
        password_file = Path(os.environ.get("RESTIC_PASSWORD_FILE", ""))
        if not password_file.is_absolute() or password_file.is_symlink():
            raise BackupError("private-restic-password-file-required")
        info = password_file.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BackupError("unsafe-restic-password-file-permissions")
        if os.environ.get("RESTIC_PASSWORD_COMMAND") or os.environ.get("RESTIC_REPOSITORY_FILE"):
            raise BackupError("use-restic-password-file-instead-of-command")
        self.runner.json([*self.prefix, "cat", "config"], "repository-preflight")

    def snapshots(self, tags: str = COMPLETE) -> list[dict[str, Any]]:
        result = self.runner.json(
            [
                *self.prefix,
                "snapshots",
                "--host",
                self.deployment_id,
                "--tag",
                self.tag + "," + tags,
            ],
            "list-qualified-snapshots",
            max_bytes=32 * 1024 * 1024,
        )
        if not isinstance(result, list):
            raise BackupError("invalid-snapshot-list")
        for snapshot in result:
            if (
                not re.fullmatch(r"[0-9a-f]{64}", snapshot.get("id", ""))
                or snapshot.get("hostname") != self.deployment_id
                or not {self.tag, *tags.split(",")}.issubset(snapshot.get("tags", []))
            ):
                raise BackupError("snapshot-filter-mismatch")
        return result

    def select(self, identifier: str) -> dict[str, Any]:
        candidates = self.snapshots()
        if identifier == "latest" and candidates:
            return max(candidates, key=lambda item: datetime.fromisoformat(item["time"]))
        matches = [snapshot for snapshot in candidates if snapshot["id"] == identifier]
        if len(matches) != 1:
            raise BackupError("complete-snapshot-not-found")
        return matches[0]

    def restore(self, snapshot: dict[str, Any], target: Path, max_bytes: int) -> Path:
        paths = snapshot.get("paths", [])
        if (
            len(paths) != 1
            or not paths[0].startswith("/")
            or ".." in Path(paths[0]).parts
            or Path(paths[0]).name != "bundle"
        ):
            raise BackupError("unexpected-snapshot-source-path")
        if target.exists() or target.is_symlink():
            raise BackupError("restore-target-must-not-exist")
        private_directory(target)
        self.runner.run(
            [*self.prefix, "restore", snapshot["id"], "--target", str(target), "--verify"],
            "restore-snapshot",
            max_bytes=16 * 1024 * 1024,
        )
        # Use an absolute source for a deterministic restored tree. Restic's paths metadata
        # is absolute even for relative inputs, whose actual restore tree is different.
        restored = target / paths[0].lstrip("/")
        if not restored.resolve().is_relative_to(target.resolve()):
            raise BackupError("restored-bundle-outside-target")
        bundle.verify(restored, self.deployment_id, max_bytes)
        return restored

    def store(self, directory: Path, run_id: str, verification_target: Path, max_bytes: int) -> str:
        run_tag = "uta-run:" + run_id
        # Nonzero includes Restic's partial-read exit status 3. Never promote it.
        self.runner.run(
            [
                *self.prefix,
                "backup",
                "--host",
                self.deployment_id,
                "--tag",
                self.tag + "," + PENDING + "," + run_tag,
                "--",
                str(directory.resolve()),
            ],
            "upload-pending-snapshot",
            cwd=directory.parent,
            max_bytes=16 * 1024 * 1024,
        )
        pending = self.snapshots(PENDING + "," + run_tag)
        if len(pending) != 1:
            raise BackupError("pending-snapshot-not-found")
        self.runner.run([*self.prefix, "check"], "repository-integrity-check")
        self.restore(pending[0], verification_target, max_bytes)
        self.runner.run(
            [*self.prefix, "tag", "--remove", PENDING, "--add", COMPLETE, pending[0]["id"]],
            "promote-verified-snapshot",
        )
        completed = self.snapshots(COMPLETE + "," + run_tag)
        if len(completed) != 1:
            raise BackupError("completed-snapshot-not-found")
        return completed[0]["id"]

    def retain(self, *, last: int, daily: int, weekly: int, monthly: int, dry_run: bool) -> None:
        if last < 1 or min(daily, weekly, monthly) < 0:
            raise BackupError("retention-must-keep-at-least-one-complete-snapshot")
        self.runner.run(
            [
                *self.prefix,
                "forget",
                "--host",
                self.deployment_id,
                "--tag",
                self.tag + "," + COMPLETE,
                "--group-by",
                "host",
                "--keep-last",
                str(last),
                "--keep-daily",
                str(daily),
                "--keep-weekly",
                str(weekly),
                "--keep-monthly",
                str(monthly),
                *(["--dry-run"] if dry_run else ["--prune"]),
            ],
            "retention",
            max_bytes=16 * 1024 * 1024,
        )
        if not dry_run:
            self.runner.run([*self.prefix, "check"], "post-retention-integrity-check")
