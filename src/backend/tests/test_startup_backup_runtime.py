"""Automatic pre-migration recovery against real PostgreSQL and Restic, including a restore drill."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.database import migrate, startup_backup
from src.database.backup_gate import RecoveryPointError
from src.database.migration_safety import recovery_directory
from tests.test_migration_backup_runtime import installation as installation  # noqa: F401
from tests.test_migration_backup_runtime import point, preserved, reports

AVATAR = b"\x00ordinary-user-file\xff"


@pytest.fixture
def automatic(installation, monkeypatch):  # noqa: F811
    """A populated installation one revision behind, with app files and no prior receipt."""
    monkeypatch.setattr(settings, "MIGRATION_BACKUP_MODE", "automatic")
    data = Path(os.environ["APP_DATA_DIR"])
    (data / "uploads").mkdir(parents=True)
    (data / "uploads" / "avatar.bin").write_bytes(AVATAR)
    (data / "cache").mkdir()
    (data / "cache" / "scratch.tmp").write_bytes(b"disposable")
    return installation


def repository_tool():
    backup = startup_backup.StartupBackup(Path(os.environ["APP_DATA_DIR"]), recovery_directory())

    def run(*arguments: str) -> bytes:
        return backup.runner.run(
            [*backup.prefix, *arguments],
            "test-restic",
            environment=backup.environment,
            inherit_environment=False,
        )

    return run


def snapshots() -> list[dict]:
    return json.loads(repository_tool()("snapshots") or b"[]") or []


def test_portainer_upgrade_creates_recovery_without_previous_receipt(automatic):
    assert not (recovery_directory() / "point.json").exists()
    migrate.main()
    preserved(automatic, automatic[2])
    report = reports()[-1]
    assert report["status"] == "succeeded" and report["mode"] == "automatic"
    assert report["verification"] == "restic-readback-and-postgresql-archive-v1"
    found = snapshots()
    assert len(found) == 1 and found[0]["id"] == report["backup_id"]
    assert startup_backup.COMPLETE in found[0]["tags"]
    assert startup_backup.PENDING not in found[0]["tags"]
    for path in recovery_directory().glob("*"):
        assert path.stat().st_mode & 0o077 == 0
        assert settings.SECRET_KEY not in path.read_text(errors="ignore")


def test_unchanged_start_makes_no_new_snapshot(automatic):
    migrate.main()
    migrate.main()
    assert len(snapshots()) == 1
    assert [item["status"] for item in reports()] == ["succeeded", "succeeded"]
    assert sum(item["backup_id"] is not None for item in reports()) == 1


def test_fresh_database_needs_no_backup_or_repository(tmp_path, monkeypatch):
    source_url = make_url(settings.DATABASE_URL.replace("+asyncpg", "+psycopg"))
    admin = create_engine(source_url, isolation_level="AUTOCOMMIT")
    name = "fresh_" + uuid4().hex
    with admin.connect() as connection:
        connection.execute(text('CREATE DATABASE "' + name + '"'))
    try:
        url = source_url.set(database=name).render_as_string(hide_password=False)
        monkeypatch.setattr(settings, "DATABASE_URL", url)
        monkeypatch.setattr(settings, "MIGRATION_BACKUP_MODE", "automatic")
        monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
        migrate.main()
        assert reports()[-1]["status"] == "succeeded" and reports()[-1]["backup_id"] is None
        assert not (tmp_path / "data" / "backups").exists()
    finally:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        admin.dispose()


def test_restored_snapshot_recovers_database_files_and_key(automatic, tmp_path):
    """The drill the startup check cannot do itself: restore into a separate database."""
    migrate.main()
    target = tmp_path / "restored"
    repository_tool()("restore", snapshots()[0]["id"], "--target", str(target))
    data = target / str(Path(os.environ["APP_DATA_DIR"]).resolve()).lstrip("/")
    assert (data / "uploads" / "avatar.bin").read_bytes() == AVATAR
    # Cache, the repository and its password never recurse into the snapshot.
    assert not (data / "cache").exists()
    assert not (data / "backups" / "startup-recovery").exists()
    assert not (data / "config" / "migration-recovery").exists()
    bundle = next(target.glob("tmp/uta-startup-recovery-*/bundle"))
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["source_revisions"] == [automatic[1]]
    assert (bundle / "recovery-keys.json").read_bytes() and json.loads(
        (bundle / "recovery-keys.json").read_bytes()
    ) == {"SECRET_KEY": settings.SECRET_KEY}

    url = automatic[0].url
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = "restore_" + uuid4().hex
    with admin.connect() as connection:
        connection.execute(text('CREATE DATABASE "' + name + '"'))
    try:
        subprocess.run(
            [
                "pg_restore",
                "--no-owner",
                "--exit-on-error",
                "--host",
                str(url.host),
                "--port",
                str(url.port or 5432),
                "--username",
                str(url.username),
                "--dbname",
                name,
                str(bundle / "database.dump"),
            ],
            env={**os.environ, "PGPASSWORD": str(url.password)},
            check=True,
            timeout=120,
        )
        restored = create_engine(url.set(database=name))
        with restored.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version"))
            assert version.scalar_one() == automatic[1]
            rows = connection.execute(text("SELECT value FROM gate_preserved")).scalars().all()
            assert rows == ["ordinary-user-record"]
        restored.dispose()
    finally:
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        admin.dispose()


@pytest.mark.parametrize("failure", ["store", "repository"])
def test_failed_automatic_backup_stops_before_any_ddl(automatic, monkeypatch, failure):
    if failure == "store":

        def broken(*_args, **_kwargs):
            raise startup_backup.BackupError("store-startup-backup", 1)

        monkeypatch.setattr(startup_backup.StartupBackup, "_store", broken)
    else:
        repository = Path(os.environ["APP_DATA_DIR"]) / "backups" / "startup-recovery"
        repository.mkdir(parents=True, mode=0o700)
        (repository / "unrelated-file").write_text("not a restic repository")
    monkeypatch.setattr(
        migrate.command, "upgrade", lambda *_args: pytest.fail("backup failure reached DDL")
    )
    with pytest.raises(RecoveryPointError, match="automatic-startup-backup-failed"):
        migrate.main()
    preserved(automatic, automatic[1])
    assert reports()[-1]["status"] == "failed"
    assert not any(item.get("resulting_revisions") for item in reports())


def test_inherited_restic_and_proxy_environment_is_ignored(automatic, monkeypatch):
    monkeypatch.setenv("RESTIC_PASSWORD_COMMAND", "false")
    monkeypatch.setenv("RESTIC_REPOSITORY", "s3:https://invalid.example/not-used")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    migrate.main()
    preserved(automatic, automatic[2])
    assert len(snapshots()) == 1


def test_symlinked_repository_path_is_refused(automatic, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (Path(os.environ["APP_DATA_DIR"]) / "backups").symlink_to(outside)
    with pytest.raises(RecoveryPointError, match="symlink"):
        migrate.main()
    assert not list(outside.iterdir())
    preserved(automatic, automatic[1])


def test_retention_keeps_only_recent_complete_snapshots(automatic, monkeypatch):
    monkeypatch.setattr(startup_backup, "KEEP_LAST", 2)
    claims = {
        "database_fingerprint": "f" * 64,
        "source_revisions": [automatic[1]],
        "target_revisions": [automatic[2]],
        "target_code_sha256": "0" * 64,
    }
    for _ in range(4):
        startup_backup.StartupBackup(
            Path(os.environ["APP_DATA_DIR"]), recovery_directory()
        ).capture(automatic[0].url, secret=settings.SECRET_KEY, claims=claims)
    assert len(snapshots()) == 2


def test_concurrent_automatic_starts_create_one_verified_snapshot(automatic):
    environment = {
        **os.environ,
        "DATABASE_URL": settings.DATABASE_URL,
        "SECRET_KEY": settings.SECRET_KEY,
        "MIGRATION_BACKUP_MODE": "automatic",
        "ENV_FILE": "/dev/null",
    }
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)
    children = [
        subprocess.Popen(
            [sys.executable, "-m", "src.database.migrate"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for _ in range(3)
    ]
    try:
        for child in children:
            output, _ = child.communicate(timeout=240)
            assert child.returncode == 0, output.decode()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
    preserved(automatic, automatic[2])
    assert len(snapshots()) == 1
    assert sum(item["backup_id"] is not None for item in reports()) == 1


def test_valid_operator_receipt_is_used_instead_of_a_second_backup(automatic):
    point(automatic)
    migrate.main()
    preserved(automatic, automatic[2])
    assert reports()[-1]["backup_id"] == "a" * 64
    assert not (Path(os.environ["APP_DATA_DIR"]) / "backups").exists()
