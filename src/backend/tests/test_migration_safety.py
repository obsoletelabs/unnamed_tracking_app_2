"""Startup must never infer that an unfamiliar populated database is safe to replay."""

import os
import runpy
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import settings
from src.database import migrate


def test_cli_database_failure_does_not_print_connection_or_statement_secrets(monkeypatch, capsys):
    def fail(*_arguments, **_options):
        raise SQLAlchemyError("postgresql://user:private-password@db/app; private-row-value")

    monkeypatch.setattr(sqlalchemy, "create_engine", fail)
    monkeypatch.setattr(sys, "argv", ["migration-runner"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(migrate.__file__, run_name="__main__")
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert "Database operation failed (SQLAlchemyError)" in output.err
    assert "private-password" not in output.out + output.err
    assert "private-row-value" not in output.out + output.err


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """Only create/drop this test's randomly named schema in the development database."""
    # This suite isolates history policy; required recovery evidence is exercised
    # against real isolated databases in test_migration_backup_runtime.py.
    monkeypatch.setattr(settings, "MIGRATION_BACKUP_MODE", "development")
    monkeypatch.setenv("MIGRATION_BACKUP_MODE", "development")
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    schema = "migration_safety_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
    admin = create_engine(base)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = base.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setattr(settings, "DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = create_engine(scoped)
    monkeypatch.setattr(migrate, "_engine", lambda: engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def marker(engine, revision=None):
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE preservation_probe (id integer PRIMARY KEY, value text)")
        )
        connection.execute(text("INSERT INTO preservation_probe VALUES (7, 'retained fixture')"))
        if revision is not None:
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)"
                )
            )
            connection.execute(text("DELETE FROM alembic_version"))
            connection.execute(
                text("INSERT INTO alembic_version VALUES (:version)"), {"version": revision}
            )


def preserved(engine, revision):
    with engine.connect() as connection:
        assert connection.execute(text("SELECT * FROM preservation_probe")).all() == [
            (7, "retained fixture")
        ]
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == revision


@pytest.mark.parametrize("revision", [None, "orphaned-release"])
def test_unrecognized_populated_history_is_unchanged_and_never_invokes_alembic(
    sandbox, monkeypatch, revision
):
    marker(sandbox, revision)
    calls = []
    monkeypatch.setattr(command, "upgrade", lambda *_a, **_kw: calls.append("upgrade"))
    monkeypatch.setattr(command, "stamp", lambda *_a, **_kw: calls.append("stamp"))
    with pytest.raises(SystemExit, match="Automatic stamping/replay is disabled"):
        migrate.main()
    assert calls == []
    with sandbox.connect() as connection:
        assert connection.scalar(text("SELECT value FROM preservation_probe")) == "retained fixture"
        if revision is None:
            assert connection.scalar(text("SELECT to_regclass('alembic_version')")) is None
        else:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == revision


def test_divergent_recorded_revisions_are_not_treated_as_a_normal_upgrade(sandbox):
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    head = script.get_current_head()
    marker(sandbox, head)
    with sandbox.begin() as connection:
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:revision)"),
            {"revision": script.get_bases()[0]},
        )
    with pytest.raises(SystemExit, match="multiple recorded revisions"):
        migrate.main()
    with sandbox.connect() as connection:
        assert set(connection.scalars(text("SELECT version_num FROM alembic_version"))) == {
            head,
            script.get_bases()[0],
        }


def test_known_head_with_schema_drift_stops_before_upgrade(sandbox, monkeypatch):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    marker(sandbox)
    with sandbox.begin() as connection:
        connection.execute(text("ALTER TABLE job_settings DROP COLUMN paused"))
    calls = []
    monkeypatch.setattr(command, "upgrade", lambda *_a, **_kw: calls.append("upgrade"))
    monkeypatch.setattr(command, "stamp", lambda *_a, **_kw: calls.append("stamp"))
    with pytest.raises(SystemExit, match="column job_settings.paused"):
        migrate.main()
    assert calls == []
    preserved(sandbox, head)


def test_supported_upgrade_and_unchanged_restart_preserve_existing_records(sandbox):
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    head = script.get_revision(script.get_current_head())
    command.upgrade(cfg, head.down_revision)
    marker(sandbox)
    migrate.main()
    migrate.main()
    preserved(sandbox, head.revision)


def test_reviewed_historical_merge_parents_still_upgrade_normally(sandbox):
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    merge = next(
        revision
        for revision in script.walk_revisions()
        if isinstance(revision.down_revision, tuple)
    )
    command.upgrade(cfg, "head")
    marker(sandbox)
    command.stamp(cfg, merge.down_revision, purge=True)
    migrate.main()
    preserved(sandbox, script.get_current_head())


def test_fresh_install_is_still_supported(sandbox):
    migrate.main()
    assert migrate.missing_from_database(sandbox) == []


def test_explicit_repair_replays_known_history_without_losing_rows(sandbox):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    marker(sandbox, "orphaned-release")
    migrate.main(allow_repair=True)
    preserved(sandbox, ScriptDirectory.from_config(cfg).get_current_head())


@pytest.mark.parametrize("failure", ["migration", "schema"])
def test_failed_explicit_repair_rolls_back_revision_data_and_ddl(sandbox, monkeypatch, failure):
    marker(sandbox, "orphaned-release")
    cfg = Config("alembic.ini")

    def destructive_upgrade(config, _revision):
        connection = config.attributes["connection"]
        connection.execute(text("DELETE FROM preservation_probe"))
        connection.execute(text("CREATE TABLE repair_probe (id integer)"))
        if failure == "migration":
            raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(command, "upgrade", destructive_upgrade)
    monkeypatch.setattr(migrate, "missing_from_database", lambda _bind: ["column unresolved"])
    with pytest.raises((RuntimeError, SystemExit), match="synthetic migration failure|rolled back"):
        migrate._reattach(cfg, ScriptDirectory.from_config(cfg).get_bases()[0], sandbox)
    preserved(sandbox, "orphaned-release")
    with sandbox.connect() as connection:
        assert connection.scalar(text("SELECT to_regclass('repair_probe')")) is None
    assert "connection" not in cfg.attributes


def test_read_only_plan_never_migrates_a_supported_older_revision(sandbox, monkeypatch, capsys):
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    previous = script.get_revision(script.get_current_head()).down_revision
    command.upgrade(cfg, previous)
    marker(sandbox)
    monkeypatch.setattr(command, "upgrade", lambda *_a, **_kw: pytest.fail("plan mutated schema"))
    migrate.main(plan_only=True)
    preserved(sandbox, previous)
    assert "Inspection only" in capsys.readouterr().out


def test_lock_contention_fails_without_stealing_lock_and_can_retry(sandbox, monkeypatch):
    monkeypatch.setattr(migrate, "_LOCK_WAIT_SECONDS", 0)
    with sandbox.connect() as owner:
        owner.execute(text("SELECT pg_advisory_lock(:id)"), {"id": migrate._LOCK_ID})
        with pytest.raises(SystemExit, match="Another instance holds the migration lock"):
            with migrate._migration_lock(sandbox):
                pytest.fail("a second worker entered the migration critical section")
        assert owner.scalar(text("SELECT pg_advisory_unlock(:id)"), {"id": migrate._LOCK_ID})
    with migrate._migration_lock(sandbox):
        pass


def test_two_real_startup_processes_serialize_and_recheck_the_revision(sandbox):
    environment = {**os.environ, "DATABASE_URL": settings.DATABASE_URL}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)
    arguments = [sys.executable, "-m", "src.database.migrate"]
    processes = [
        subprocess.Popen(
            arguments,
            cwd=Path(__file__).parents[1],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            assert process.returncode == 0, stdout + stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    assert migrate.missing_from_database(sandbox) == []
