"""End-to-end restore drill on synthetic data: back up, restore into a new database, upgrade.

A populated database at an older revision is backed up with the application's own startup
backup (real Restic and PostgreSQL client tools), the encrypted repository is restored into an
empty directory and the dump into a NEW database (on a second server when
``RESTORE_DRILL_TARGET_URL`` is set), and the upgrade is interrupted by killing the migrator's
database session while a table lock holds it mid-upgrade. The retry must reach head with the
same invariants the upgrade matrix checks: rows, owners, values, foreign keys and file
checksums. Damaged backups must be detected, not restored. Nothing here touches the configured
database beyond creating and dropping its own databases; the report names steps, tables and
counts only.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet, InvalidToken
from psycopg import sql
from sqlalchemy.engine import URL, make_url

from src.core.backup_process import BackupError
from src.core.config import settings
from src.core.crypto import encrypt_secret
from src.database import restore_drill
from src.database.backup_gate import database_fingerprint
from src.database.startup_backup import StartupBackup
from tests.test_migration_upgrade_matrix import (
    BACKEND,
    STARTS,
    _alembic,
    _checksums,
    _foreign_keys_hold,
    _populate,
    _snapshot,
    compare,
)

START = STARTS[3][0]  # the reconciliation of three divergent lines: many revisions are pending
CANARY = "synthetic-drill-canary"


def _admin(url: URL) -> str:
    return url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )


def _plain(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _environment(url: URL) -> dict[str, str]:
    environment = {**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)
    return environment


@contextmanager
def new_database(server: URL, prefix: str):
    """Create a database that exists only for this drill and always drop it afterwards."""
    name = f"{prefix}_{uuid4().hex}"
    with psycopg.connect(_admin(server), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield server.set(database=name)
    finally:
        with psycopg.connect(_admin(server), autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


class Source:
    """The deployment being backed up: a populated database, files, key and Restic repository."""

    def __init__(self, root: Path, server: URL, database: URL):
        self.root = root
        self.server = server
        self.url = database
        self.data = root / "data"
        self.recovery = root / "recovery"

    def build(self) -> None:
        _alembic(_environment(self.url), "upgrade", START)
        with psycopg.connect(_plain(self.url), autocommit=True) as connection:
            self.populated = _populate(connection, "public")
            assert {"users", "games", "media_items"} <= set(self.populated)
            # Make the one media row a real screenshot with a file where the app keeps it.
            connection.execute("UPDATE media_items SET kind = 'screenshot', deleted_at = NULL")
            for user_id, folder, filename in connection.execute(
                "SELECT g.user_id, g.folder_location, m.filename FROM media_items m"
                " JOIN games g ON g.id = m.game_id"
            ).fetchall():
                path = self.data / str(user_id) / "games" / folder / "screenshots" / filename
                path.parent.mkdir(parents=True)
                path.write_bytes(b"\x00synthetic screenshot\xff" * 128)
            self.before = _snapshot(connection, "public")
            self.references = restore_drill.missing_file_references(connection, self.data)
        (self.data / "config").mkdir()
        (self.data / "config" / "canary.enc").write_text(encrypt_secret(CANARY), encoding="ascii")
        (self.data / "cache").mkdir()
        (self.data / "cache" / "scratch.tmp").write_bytes(b"not part of a backup")
        self.checksums = {
            name: digest
            for name, digest in _checksums(self.data).items()
            if not name.startswith(("cache", "backups"))
        }
        self.backup = StartupBackup(self.data, self.recovery)
        self.backup.capture(
            self.url,
            secret=settings.SECRET_KEY,
            claims={"database_fingerprint": database_fingerprint(self.url, settings.SECRET_KEY)},
        )

    def copy_repository(self, destination: Path) -> StartupBackup:
        """A throwaway copy of the repository and its password for damaging."""
        shutil.copytree(self.data, destination / "data", symlinks=True)
        shutil.copytree(self.recovery, destination / "recovery")
        return StartupBackup(destination / "data", destination / "recovery")


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    server = make_url(settings.DATABASE_URL.replace("+asyncpg", "+psycopg"))
    with new_database(server, "drill_source") as database:
        root = tmp_path_factory.mktemp("drill-source")
        deployment = Source(root, server, database)
        deployment.build()
        yield deployment


def _target_server(source: Source) -> URL:
    configured = os.environ.get("RESTORE_DRILL_TARGET_URL")
    if configured:
        return make_url(configured.replace("+asyncpg", "+psycopg"))
    return source.server


def _kill_migrator_while_blocked(target: URL, root: Path) -> None:
    """Hold a lock a pending migration needs, then end its session mid-upgrade."""
    log = root / "interrupted.log"
    # Activity statistics are frozen per transaction, so watch from an autocommit session.
    with (
        psycopg.connect(_plain(target)) as holder,
        psycopg.connect(_plain(target), autocommit=True) as observer,
    ):
        holder.execute("LOCK TABLE users IN ACCESS EXCLUSIVE MODE")
        with log.open("w", encoding="utf-8") as output:
            child = subprocess.Popen(
                [sys.executable, "-m", "src.database.migrate"],
                cwd=BACKEND,
                env={
                    **_environment(target),
                    "MIGRATION_BACKUP_MODE": "development",
                    "ENV_FILE": os.devnull,
                    "APP_DATA_DIR": str(root / "migrator"),
                },
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 60
                victim = None
                while victim is None:
                    assert child.poll() is None, "the migrator finished without being blocked"
                    assert time.monotonic() < deadline, "no migration waited for the table lock"
                    row = observer.execute(
                        "SELECT pid FROM pg_stat_activity WHERE datname = current_database()"
                        " AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                    ).fetchone()
                    victim = row[0] if row else None
                    time.sleep(0.1)
                observer.execute("SELECT pg_terminate_backend(%s)", (victim,))
                child.wait(timeout=60)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)
        assert child.returncode != 0, "an interrupted upgrade must not report success"
    assert "Database is up to date" not in log.read_text(encoding="utf-8")


def _retry_upgrade(target: URL, root: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "src.database.migrate"],
        cwd=BACKEND,
        env={
            **_environment(target),
            "MIGRATION_BACKUP_MODE": "development",
            "ENV_FILE": os.devnull,
            "APP_DATA_DIR": str(root / "migrator"),
        },
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, "the retried upgrade failed:\n" + result.stderr[-1500:]
    assert "Database is up to date." in result.stdout


def test_restore_then_interrupted_upgrade_keeps_every_invariant(source, tmp_path):
    report = restore_drill.DrillReport()
    began = time.monotonic()
    try:
        with report.step("restore the encrypted repository into an empty directory"):
            restored = tmp_path / "restored-files"
            restored.mkdir()
            bundle = restore_drill.restore_snapshot(source.backup, restored)
            assert restore_drill.bundle_problems(bundle) == []
        with report.step("restored files and encryption key match the original"):
            data = restored / str(source.data.resolve()).lstrip("/")
            restored_checksums = _checksums(data)
            assert {
                name: digest
                for name, digest in restored_checksums.items()
                if name in source.checksums
            } == source.checksums
            key = (bundle / "recovery-keys.json").read_text(encoding="utf-8")
            assert settings.SECRET_KEY in key
            canary = (data / "config" / "canary.enc").read_text(encoding="ascii")
            assert Fernet(settings.SECRET_KEY.encode()).decrypt(canary.encode()).decode() == CANARY
        with new_database(_target_server(source), "drill_restore") as target:
            with report.step("restore the dump into a new, empty database"):
                restore_drill.restore_database(
                    bundle / "database.dump", target, protected=make_url(settings.DATABASE_URL)
                )
            with report.step("restored database equals the backed-up state"):
                with psycopg.connect(_plain(target), autocommit=True) as connection:
                    assert compare(source.before, _snapshot(connection, "public")) == []
                    assert _snapshot(connection, "public") == source.before
                    assert _foreign_keys_hold(connection, "public") == []
                    assert restore_drill.missing_file_references(connection, data) == []
            with report.step("interrupt the upgrade partway"):
                _kill_migrator_while_blocked(target, tmp_path)
            with report.step("retry the upgrade to head"):
                _retry_upgrade(target, tmp_path)
            with report.step("rows, owners, values, foreign keys and files survive"):
                with psycopg.connect(_plain(target), autocommit=True) as connection:
                    assert compare(source.before, _snapshot(connection, "public")) == []
                    assert _foreign_keys_hold(connection, "public") == []
                    assert restore_drill.missing_file_references(connection, data) == []
                assert "(head)" in _alembic(_environment(target), "current")
                assert _checksums(data) == restored_checksums
        report.measurements["restore_and_verified_upgrade_seconds"] = round(
            time.monotonic() - began, 1
        )
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        report.measurements["backup_age_seconds_at_drill_start"] = int(
            report.started_at - manifest["captured_at"]
        )
    finally:
        rendered_json, rendered_markdown = report.render((settings.SECRET_KEY,))
        directory = os.environ.get("RESTORE_DRILL_REPORT_DIR")
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            (Path(directory) / "restore-drill.json").write_text(rendered_json, encoding="utf-8")
            (Path(directory) / "restore-drill.md").write_text(rendered_markdown, encoding="utf-8")
        assert settings.SECRET_KEY not in rendered_json + rendered_markdown
    assert report.passed


# ------------------------------------------------------------------ damaged backups ---


def _packs(backup: StartupBackup) -> list[Path]:
    return sorted(
        (path for path in (backup.repository / "data").rglob("*") if path.is_file()),
        key=lambda path: path.stat().st_size,
        reverse=True,
    )


def _truncate(backup: StartupBackup) -> None:
    pack = _packs(backup)[0]
    pack.write_bytes(pack.read_bytes()[: pack.stat().st_size // 2])


def _flip(backup: StartupBackup) -> None:
    pack = _packs(backup)[0]
    content = bytearray(pack.read_bytes())
    content[len(content) // 2] ^= 0xFF
    pack.write_bytes(bytes(content))


def _delete_pack(backup: StartupBackup) -> None:
    _packs(backup)[0].unlink()


def _remove_snapshots(backup: StartupBackup) -> None:
    shutil.rmtree(backup.repository / "snapshots")


def _lose_keys(backup: StartupBackup) -> None:
    shutil.rmtree(backup.repository / "keys")


def _wrong_password(backup: StartupBackup) -> None:
    backup.password_file.chmod(0o600)
    backup.password_file.write_text("A" * 64 + "\n", encoding="ascii")


@pytest.mark.parametrize(
    "damage",
    [_truncate, _flip, _delete_pack, _remove_snapshots, _lose_keys, _wrong_password],
    ids=lambda damage: damage.__name__.strip("_"),
)
def test_damaged_repositories_are_detected_before_anything_is_restored(source, tmp_path, damage):
    copy = source.copy_repository(tmp_path)
    damage(copy)
    destination = tmp_path / "restore"
    destination.mkdir()
    with pytest.raises(BackupError):
        restore_drill.restore_snapshot(copy, destination)
    assert list(destination.iterdir()) == []


@pytest.fixture
def bundle(source, tmp_path):
    destination = tmp_path / "bundle-restore"
    destination.mkdir()
    return restore_drill.restore_snapshot(source.backup, destination)


def test_an_undamaged_bundle_has_no_problems(bundle):
    assert restore_drill.bundle_problems(bundle) == []


@pytest.mark.parametrize("name", restore_drill.BUNDLE_FILES)
def test_an_incomplete_bundle_is_reported(bundle, name):
    (bundle / name).unlink()
    assert restore_drill.bundle_problems(bundle) == [f"missing {name}"]


def test_a_truncated_or_altered_dump_is_reported(bundle):
    dump = bundle / "database.dump"
    dump.write_bytes(dump.read_bytes()[:-100])
    assert restore_drill.bundle_problems(bundle) == [
        "database dump does not match its recorded checksum"
    ]


def test_a_bundle_without_the_encryption_key_is_reported(bundle):
    (bundle / "recovery-keys.json").write_text("{}", encoding="utf-8")
    assert restore_drill.bundle_problems(bundle) == [
        "recovery keys do not contain the encryption key"
    ]


def test_a_bundle_with_unreadable_metadata_is_reported(bundle):
    (bundle / "manifest.json").write_text("not json", encoding="utf-8")
    assert restore_drill.bundle_problems(bundle) == ["manifest or recovery keys are not valid JSON"]


def test_the_wrong_encryption_key_cannot_read_restored_secrets(bundle, source, tmp_path):
    canary = (source.data / "config" / "canary.enc").read_text(encoding="ascii")
    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(canary.encode())


def test_restore_refuses_the_configured_database_and_a_populated_one(source, bundle):
    dump = bundle / "database.dump"
    configured = make_url(settings.DATABASE_URL)
    with pytest.raises(BackupError, match="configured-database"):
        restore_drill.restore_database(dump, configured, protected=configured)
    with pytest.raises(BackupError, match="not-empty"):
        restore_drill.restore_database(dump, source.url, protected=configured)


# ----------------------------------------------------------- database-to-file links ---


def test_missing_or_misplaced_files_break_the_database_to_file_check(source, tmp_path):
    copy = tmp_path / "data"
    shutil.copytree(source.data, copy)
    with psycopg.connect(_plain(source.url)) as connection:
        assert restore_drill.missing_file_references(connection, copy) == []
        victim = next(path for path in copy.rglob("screenshots/*") if path.is_file())
        digest = hashlib.sha256(victim.read_bytes()).hexdigest()
        victim.unlink()
        assert restore_drill.missing_file_references(connection, copy) == [
            "media_items: 1 of 1 referenced file(s) missing"
        ]
        victim.parent.parent.joinpath("clips").mkdir()
        victim.parent.parent.joinpath("clips", victim.name).write_bytes(b"in the wrong folder")
        assert restore_drill.missing_file_references(connection, copy) != []
        assert digest  # the report would name the table and a count, never the file


def test_the_report_removes_secrets_and_explains_failure():
    report = restore_drill.DrillReport()
    with pytest.raises(RuntimeError):
        with report.step("restore"):
            raise RuntimeError("could not use hunter2")
    rendered_json, rendered_markdown = report.render(("hunter2",))
    assert "hunter2" not in rendered_json + rendered_markdown
    assert '"result": "failed"' in rendered_json
    assert "runbook" in rendered_markdown
