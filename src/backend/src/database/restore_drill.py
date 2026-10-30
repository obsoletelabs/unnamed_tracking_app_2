"""Checks for a restored backup: is the archive whole, and does the restored copy hold together?

The drill (``tests/test_restore_drill.py``) and the operator runbook in the wiki both rely on
these. Nothing here writes to an existing database: the restore target must be a new, empty
database that is not the one the application is configured to use, and every message names
tables, files and steps only, never row values, keys or paths under a user's data directory.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from src.core.backup_process import BackupError, Runner
from src.database.startup_backup import COMPLETE, StartupBackup, _digest, _libpq_environment
from src.helpers.media import media_subdir

BUNDLE_FILES = ("manifest.json", "recovery-keys.json", "database.dump")


def _restic(backup: StartupBackup, arguments: list[str], step: str) -> bytes:
    return backup.runner.run(
        [*backup.prefix, *arguments],
        step,
        environment=backup.environment,
        inherit_environment=False,
    )


def restore_snapshot(backup: StartupBackup, target: Path, *, read_data: bool = True) -> Path:
    """Restore the newest complete snapshot into an empty directory and return its bundle.

    ``restic check`` (reading every pack when ``read_data``) runs first so truncated,
    corrupt, incomplete or wrongly keyed repositories fail before anything is restored.
    """
    _restic(backup, ["check", *(["--read-data"] if read_data else [])], "check-repository")
    try:
        snapshots = json.loads(_restic(backup, ["snapshots", "--tag", COMPLETE], "list-snapshots"))
    except ValueError:
        raise BackupError("list-snapshots:invalid-json") from None
    if not isinstance(snapshots, list) or not snapshots:
        raise BackupError("no-complete-snapshot")
    newest = max(snapshots, key=lambda item: item["time"])
    _restic(backup, ["restore", newest["id"], "--target", str(target), "--verify"], "restore")
    bundles = list(target.glob("**/uta-startup-recovery-*/bundle"))
    if len(bundles) != 1:
        raise BackupError("snapshot-has-no-single-bundle")
    return bundles[0]


def bundle_problems(bundle: Path) -> list[str]:
    """What is wrong with a restored bundle, as short codes (empty when it is usable)."""
    problems = [f"missing {name}" for name in BUNDLE_FILES if not (bundle / name).is_file()]
    if problems:
        return problems
    try:
        manifest = json.loads((bundle / "manifest.json").read_bytes())
        keys = json.loads((bundle / "recovery-keys.json").read_bytes())
    except ValueError:
        return ["manifest or recovery keys are not valid JSON"]
    if not isinstance(manifest, dict) or manifest.get("kind") != "app-local-startup-recovery-v1":
        problems.append("manifest is not a startup recovery manifest")
    if not isinstance(keys, dict) or not keys.get("SECRET_KEY"):
        problems.append("recovery keys do not contain the encryption key")
    if isinstance(manifest, dict) and _digest(bundle / "database.dump") != manifest.get(
        "database_sha256"
    ):
        problems.append("database dump does not match its recorded checksum")
    return problems


def ensure_new_database(target: URL, *, protected: URL) -> None:
    """Refuse to restore into the database the application is configured to use."""
    if (target.host, target.port or 5432, target.database) == (
        protected.host,
        protected.port or 5432,
        protected.database,
    ):
        raise BackupError("restore-target-is-the-configured-database")


def restore_database(dump: Path, target: URL, *, protected: URL) -> None:
    """``pg_restore`` into an existing but empty database; anything else is refused."""
    ensure_new_database(target, protected=protected)
    engine = create_engine(target)
    try:
        with engine.connect() as connection:
            tables = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    if tables:
        raise BackupError("restore-target-is-not-empty")
    Runner(1800).run(
        [
            "pg_restore",
            "--no-owner",
            "--exit-on-error",
            "--dbname",
            str(target.database),
            str(dump),
        ],
        "restore-database",
        environment={"PATH": "/usr/bin:/bin:/usr/local/bin", **_libpq_environment(target)},
        inherit_environment=False,
    )


def missing_file_references(connection: psycopg.Connection, data_root: Path) -> list[str]:
    """Media rows whose file is gone, counted per table.

    A file lives at ``<user>/games/<folder>/<kind folder>/<filename>`` under the data root;
    rows in the trash (``deleted_at`` set) are moved elsewhere and are not checked.
    """
    missing = 0
    total = 0
    for user_id, folder, kind, filename in connection.execute(
        "SELECT g.user_id, g.folder_location, m.kind, m.filename FROM media_items m"
        " JOIN games g ON g.id = m.game_id WHERE m.deleted_at IS NULL"
    ):
        try:
            subdirectory = media_subdir(kind)
        except KeyError:
            continue
        total += 1
        path = data_root / str(user_id) / "games" / (folder or "") / subdirectory / filename
        missing += not path.is_file()
    return [f"media_items: {missing} of {total} referenced file(s) missing"] if missing else []


@dataclass
class DrillReport:
    """What a drill did, how long each step took and what it found."""

    steps: list[dict] = field(default_factory=list)
    started_at: int = field(default_factory=lambda: int(time.time()))
    finished_at: int | None = None
    measurements: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        began = time.monotonic()
        entry = {"step": name, "status": "running", "seconds": 0.0, "detail": ""}
        self.steps.append(entry)
        try:
            yield
            entry["status"] = "passed"
        except BaseException as error:
            entry["status"] = "failed"
            entry["detail"] = f"{type(error).__name__}: {error}"[:500]
            raise
        finally:
            entry["seconds"] = round(time.monotonic() - began, 2)
            self.finished_at = int(time.time())

    @property
    def passed(self) -> bool:
        return bool(self.steps) and all(entry["status"] == "passed" for entry in self.steps)

    def render(self, secrets: tuple[str, ...] = ()) -> tuple[str, str]:
        """JSON and Markdown renderings with every given secret removed."""
        document = {
            "result": "passed" if self.passed else "failed",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "measurements": self.measurements,
            "steps": self.steps,
        }
        lines = [
            f"# Restore drill: {document['result']}",
            "",
            "| Step | Result | Seconds | Detail |",
            "| --- | --- | --- | --- |",
            *(
                f"| {entry['step']} | {entry['status']} | {entry['seconds']} |"
                f" {str(entry['detail']).replace('|', '/')} |"
                for entry in self.steps
            ),
            "",
            *(f"* {name}: {value}" for name, value in self.measurements.items()),
        ]
        if not self.passed:
            lines += ["", "Next: see the manual recovery runbook (wiki: Disaster recovery)."]
        rendered = json.dumps(document, indent=2, sort_keys=True), "\n".join(lines) + "\n"
        for secret in filter(None, secrets):
            rendered = (rendered[0].replace(secret, "***"), rendered[1].replace(secret, "***"))
        return rendered
