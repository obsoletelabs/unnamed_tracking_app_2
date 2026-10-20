"""Real PostgreSQL startup policy: no unapproved DDL, private reports and concurrent starts."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.database import migrate
from src.database.backup_gate import (
    RecoveryClaims,
    RecoveryPointError,
    database_fingerprint,
    migration_code_digest,
    write_recovery_point,
)
from src.database.migration_safety import recovery_directory


@pytest.fixture
def installation(tmp_path, monkeypatch):
    """Create and remove only this test's generated database, never the shared database."""
    source_url = make_url(settings.DATABASE_URL.replace("+asyncpg", "+psycopg"))
    admin = create_engine(source_url, isolation_level="AUTOCOMMIT")
    name = "gate_" + uuid4().hex
    with admin.connect() as connection:
        connection.execute(text('CREATE DATABASE "' + name + '"'))
    url = source_url.set(database=name)
    engine = create_engine(url)
    monkeypatch.setattr(settings, "DATABASE_URL", url.render_as_string(hide_password=False))
    monkeypatch.setattr(settings, "MIGRATION_BACKUP_MODE", "required")
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    head = script.get_current_head()
    previous = script.get_revision(head).down_revision
    assert isinstance(previous, str)
    try:
        command.upgrade(Config("alembic.ini"), "head")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE gate_preserved (value text NOT NULL)"))
            connection.execute(text("INSERT INTO gate_preserved VALUES ('ordinary-user-record')"))
        command.stamp(Config("alembic.ini"), previous)
        yield engine, previous, head
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        admin.dispose()


def point(installation, **changes):
    engine, previous, head = installation
    now = int(time.time())
    claims = RecoveryClaims(
        version=1,
        database_fingerprint=database_fingerprint(engine.url, settings.SECRET_KEY),
        source_revisions=[previous],
        target_revisions=[head],
        target_code_sha256=migration_code_digest(Path(migrate.__file__).parent),
        snapshot_id="a" * 64,
        manifest_sha256="b" * 64,
        source_image_id="sha256:" + "c" * 64,
        captured_at=now,
        verified_at=now,
        verification="isolated-postgresql-restore-v1",
    ).model_copy(update=changes)
    path = recovery_directory() / "point.json"
    write_recovery_point(path, claims, settings.SECRET_KEY)
    return path


def preserved(installation, revision):
    with installation[0].connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == revision
        )
        assert connection.execute(text("SELECT value FROM gate_preserved")).scalars().all() == [
            "ordinary-user-record"
        ]


def reports():
    return [json.loads(path.read_bytes()) for path in recovery_directory().glob("attempt-*.json")]


@pytest.mark.parametrize(
    "failure", ["missing", "tampered", "stale", "database", "revision", "target", "code"]
)
def test_invalid_recovery_halts_before_pending_migration(installation, failure):
    changes = {
        "stale": {"captured_at": int(time.time()) - 8000},
        "database": {"database_fingerprint": "0" * 64},
        "revision": {"source_revisions": ["wrong"]},
        "target": {"target_revisions": ["wrong"]},
        "code": {"target_code_sha256": "0" * 64},
    }
    if failure != "missing":
        path = point(installation, **changes.get(failure, {}))
        if failure == "tampered":
            path.write_bytes(path.read_bytes().replace(b'"snapshot_id":"a', b'"snapshot_id":"b'))
    with pytest.raises(RecoveryPointError):
        migrate.main()
    preserved(installation, installation[1])
    assert reports()[-1]["status"] == "failed"
    assert not any(item.get("resulting_revisions") for item in reports())


def test_verified_upgrade_then_unchanged_start_needs_no_new_backup(installation):
    path = point(installation)
    migrate.main()
    preserved(installation, installation[2])
    path.unlink()
    migrate.main()
    assert [report["status"] for report in reports()] == ["succeeded", "succeeded"]
    assert sum(report["backup_id"] is not None for report in reports()) == 1
    for path in recovery_directory().glob("attempt-*.json"):
        assert path.stat().st_mode & 0o777 == 0o600
        assert settings.SECRET_KEY not in path.read_text()
        assert installation[0].url.password not in path.read_text()


def test_explicit_repair_cannot_bypass_recovery_gate(installation):
    command.stamp(Config("alembic.ini"), "base", purge=True)
    # An unversioned populated installation still requires both explicit review and recovery.
    with pytest.raises(RecoveryPointError, match="required"):
        migrate.main(allow_repair=True)
    with installation[0].connect() as connection:
        assert (
            connection.execute(text("SELECT value FROM gate_preserved")).scalar_one()
            == "ordinary-user-record"
        )
        assert connection.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 0


def test_at_head_repair_still_requires_backup(installation, monkeypatch):
    command.stamp(Config("alembic.ini"), installation[2])
    monkeypatch.setattr(migrate, "missing_from_database", lambda _engine: ["column missing.value"])
    with pytest.raises(RecoveryPointError, match="required"):
        migrate.main(allow_repair=True)
    preserved(installation, installation[2])


def test_development_opt_out_is_explicit_and_reported(installation, monkeypatch, capsys):
    monkeypatch.setattr(settings, "MIGRATION_BACKUP_MODE", "development")
    migrate.main()
    preserved(installation, installation[2])
    assert "Development opt-out" in capsys.readouterr().out
    assert reports()[-1]["mode"] == "development"
    assert reports()[-1]["backup_id"] is None


def test_migration_failure_is_recorded_without_success_or_secrets(installation, monkeypatch):
    point(installation)

    def broken(_cfg, _revision):
        with installation[0].begin() as connection:
            connection.execute(text("UPDATE gate_preserved SET value='changed'"))
            raise RuntimeError("sensitive-sql-and-password")

    monkeypatch.setattr(migrate.command, "upgrade", broken)
    with pytest.raises(RuntimeError):
        migrate.main()
    preserved(installation, installation[1])
    report = reports()[-1]
    assert report["status"] == "failed" and report["error"] == "RuntimeError"
    assert "sensitive-sql-and-password" not in json.dumps(report)
    assert "resulting_revisions" not in report


def test_plan_is_read_only_and_does_not_create_recovery_state(installation):
    migrate.main(plan_only=True)
    preserved(installation, installation[1])
    assert not recovery_directory().exists()


def test_concurrent_starts_share_one_verified_upgrade(installation):
    point(installation)
    environment = {
        **os.environ,
        "DATABASE_URL": settings.DATABASE_URL,
        "SECRET_KEY": settings.SECRET_KEY,
        "MIGRATION_BACKUP_MODE": "required",
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
        for _ in range(2)
    ]
    try:
        for child in children:
            output, _ = child.communicate(timeout=90)
            assert child.returncode == 0, output.decode()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
    preserved(installation, installation[2])
    assert len(reports()) == 2
    assert all(report["status"] == "succeeded" for report in reports())
    assert sum(report["backup_id"] is not None for report in reports()) == 1


def test_unreachable_database_records_failure_without_running_alembic(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))

    def unavailable(_engine):
        raise SystemExit("Database is not reachable")

    monkeypatch.setattr(migrate, "_wait_for_database", unavailable)
    monkeypatch.setattr(
        migrate.command, "upgrade", lambda *_args: pytest.fail("offline database reached DDL")
    )
    with pytest.raises(SystemExit, match="not reachable"):
        migrate.main()
    assert reports()[-1]["status"] == "failed"
    assert reports()[-1]["error"] == "SystemExit"


def test_full_audit_disk_stops_before_connecting(tmp_path, monkeypatch):
    from src.database import migration_safety

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))

    def disk_full(*_args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(migration_safety, "write_private_document", disk_full)
    monkeypatch.setattr(
        migrate, "_engine", lambda: pytest.fail("unrecordable attempt reached database")
    )
    with pytest.raises(OSError, match="No space"):
        migrate.main()
