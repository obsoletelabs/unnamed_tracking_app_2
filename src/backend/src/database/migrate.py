"""Upgrade recognized database histories under the shared migration lock.

Unknown/unversioned populated databases and skipped ancestors require operator
review. Startup never silently stamps or replays them. The explicit repair CLI
is for an operator who has already verified recovery and tested a restored clone;
it does not itself create or verify a backup. PostgreSQL repair DDL, revision
stamping and the final schema check share one transaction.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import pkgutil
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from src.database.base import Base
from src.database.migration_safety import MigrationSafety

# Starting an older build against a database a newer build already migrated is allowed when the
# schema still fits; set this to false to refuse it instead.
ALLOW_NEWER_DATABASE = "ALLOW_NEWER_DATABASE"
_LOCK_ID = 7_310_442_901
_WAIT_SECONDS = 60
_LOCK_WAIT_SECONDS = 60


@dataclass(frozen=True)
class Plan:
    action: str  # "fresh" | "upgrade" | "adopt"
    reason: str


def decide(current: set[str], known: set[str], has_tables: bool) -> Plan:
    """What to do with a database, from its recorded versions, the versions
    this code's history contains, and whether it has any tables at all."""
    if not has_tables and not current:
        return Plan("fresh", "empty database")
    if not current:
        return Plan("adopt", "tables exist but no recorded version")
    unknown = sorted(current - known)
    if unknown:
        return Plan(
            "adopt", f"recorded version {', '.join(map(ascii, unknown))} is not in this history"
        )
    return Plan("upgrade", "recorded version is in the history")


def newer_database_allowed() -> bool:
    return os.environ.get(ALLOW_NEWER_DATABASE, "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _start_on_newer_database(engine: Engine, current: set[str], known: set[str]) -> bool:
    """Whether to leave a database recorded at a version this build does not know as it is.

    This is the case after going back to an older build from a newer one. It is only allowed
    unless the operator turned it off, and only when every table and column this build's models need is still
    there, so the older code can run. The recorded version is never changed, and no migration
    is run, so going forward again resumes exactly where the newer build left off."""
    unknown = sorted(current - known)
    if not unknown or not newer_database_allowed():
        return False
    problems = missing_from_database(engine)
    if problems:
        _refuse_repair(
            f"recorded version {', '.join(map(ascii, unknown))} comes from a newer build, "
            "and this build cannot run on that schema because it lacks " + ", ".join(problems)
        )
    print(
        f"WARNING: the database was migrated by a newer build (version {', '.join(map(ascii, unknown))}). "
        "The schema still has everything this build needs, so "
        "it starts without migrating or stamping. Some newer features may not work, and newer "
        "data this build does not know about is left untouched.",
        flush=True,
    )
    return True


def _register_models() -> None:
    from src.database import models  # pylint: disable=import-outside-toplevel

    for module in pkgutil.walk_packages(models.__path__, models.__name__ + "."):
        importlib.import_module(module.name)


def missing_from_database(engine: Engine | Connection) -> list[str]:
    """What the models need that the database does not have: whole tables
    and columns. Extra things in the database (an old index, a spare
    column) are harmless and ignored."""
    # Register every model without importing the application: a broken app module
    # must fail at backend start, not be reported as a migration failure.
    _register_models()

    # Extra tables and indexes are expected here; keep the per-start check quiet.
    quiet = [logging.getLogger(name) for name in ("alembic.autogenerate", "alembic.ddl")]
    levels = [logger.level for logger in quiet]
    for logger in quiet:
        logger.setLevel(logging.WARNING)
    try:
        with nullcontext(engine) if isinstance(engine, Connection) else engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        for logger, level in zip(quiet, levels, strict=True):
            logger.setLevel(level)
    problems: list[str] = []
    for entry in diff:
        ops = entry if isinstance(entry, list) else [entry]
        for op in ops:
            kind = op[0]
            if kind == "add_table":
                problems.append(f"table {op[1].name}")
            elif kind == "add_column":
                problems.append(f"column {op[2]}.{op[3].name}")
    return problems


def _engine() -> Engine:
    # Migration planning can run without loading deployment credentials.
    from src.core.config import settings  # pylint: disable=import-outside-toplevel

    return create_engine(
        settings.DATABASE_URL.replace("+asyncpg", "+psycopg"),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def _wait_for_database(engine: Engine) -> None:
    deadline = time.time() + _WAIT_SECONDS
    last = ""
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except (SQLAlchemyError, OSError) as exc:
            last = type(exc).__name__
            if time.time() > deadline:
                raise SystemExit(
                    f"Database is not reachable after {_WAIT_SECONDS}s: {last}"
                ) from exc
            print(f"Waiting for the database ({last})...", flush=True)
            time.sleep(2)


def _reattach(cfg: Config, base: str, engine: Engine) -> None:
    """Explicit repair only; roll back stamping and transactional DDL on failure."""
    print(f"Operator-approved history replay from {base}; validating before commit.", flush=True)
    previous = cfg.attributes.get("connection")
    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.stamp(cfg, base, purge=True)
            command.upgrade(cfg, "head")
            problems = missing_from_database(connection)
            if problems:
                raise SystemExit(
                    "Repair failed schema validation: "
                    + ", ".join(problems)
                    + ". The repair transaction was rolled back. Keep the application stopped "
                    "and review the restored clone; no automatic retry or restore will run."
                )
    finally:
        if previous is None:
            cfg.attributes.pop("connection", None)
        else:
            cfg.attributes["connection"] = previous


def _repair_skipped_migrations(
    cfg: Config,
    base: str,
    engine: Engine,
    *,
    allow_repair: bool = False,
    before_repair: Callable[[], None] | None = None,
) -> None:
    """Replay the history when a recorded version claims migrations that never ran.

    A database stamped by an image whose history placed a revision on a different
    parent (for example a pre-merge branch build) records a version that main also
    knows, so Alembic believes every ancestor already ran. Checking the schema after
    the normal upgrade catches the columns and tables those skipped ancestors add."""
    problems = missing_from_database(engine)
    if not problems:
        return
    if not allow_repair:
        _refuse_repair("recognized revision has missing schema: " + ", ".join(problems))
    print(
        "The recorded database version is known, but "
        + ", ".join(problems)
        + " is missing; this may indicate skipped migrations. Operator-approved replay requested.",
        flush=True,
    )
    if before_repair is not None:
        before_repair()
    _reattach(cfg, base, engine)


def _refuse_repair(reason: str) -> None:
    raise SystemExit(
        "Migration history requires operator review: "
        + reason
        + ". Automatic stamping/replay is disabled. Keep the application stopped. "
        "Retain a restore-tested backup and matching application image, inspect the history, "
        "and test recovery on a separate restored clone. Only then run "
        "python -m src.database.migrate --repair-after-verified-backup if replay is appropriate."
    )


@contextmanager
def _migration_lock(engine: Engine):
    """Bound contention while retaining the session-level lock through all work."""
    with engine.connect() as connection:
        deadline = time.monotonic() + _LOCK_WAIT_SECONDS
        announced_wait = False
        while not connection.scalar(text("SELECT pg_try_advisory_lock(:id)"), {"id": _LOCK_ID}):
            connection.rollback()
            if not announced_wait:
                print("Waiting for another instance to finish database migrations.", flush=True)
                announced_wait = True
            if time.monotonic() >= deadline:
                raise SystemExit(
                    "Another instance holds the migration lock. No migration was attempted; "
                    "wait for that instance to finish, then retry startup."
                )
            time.sleep(1)
        connection.commit()
        try:
            yield connection
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _LOCK_ID})
            connection.commit()


def _check_recorded_history(script: ScriptDirectory, current: set[str], known: set[str]) -> None:
    if len(current) < 2 or not current <= known:
        return
    # Published histories include reviewed merge points. Independent parents may
    # upgrade normally; recording an ancestor and its descendant is inconsistent.
    for revision in current:
        ancestors = {item.revision for item in script.iterate_revisions(revision, "base")} - {
            revision
        }
        if ancestors & current:
            _refuse_repair("inconsistent multiple recorded revisions")


def _apply_plan(
    cfg: Config,
    script: ScriptDirectory,
    engine: Engine,
    connection: Connection,
    current: set[str],
    *,
    plan: Plan,
    allow_repair: bool,
    safety: MigrationSafety,
) -> None:
    target = set(script.get_heads())

    def approve() -> None:
        assert safety is not None
        safety.approve(connection, current, target)

    assert safety is not None
    safety.record("inspected", source_revisions=sorted(current), target_revisions=sorted(target))
    if plan.action == "adopt":
        if not allow_repair:
            _refuse_repair(plan.reason)
        approve()
        _reattach(cfg, script.get_bases()[0], engine)
    else:
        # At head, detect drift before even invoking Alembic. Older supported
        # revisions may legitimately lack columns added by pending migrations.
        if current == set(script.get_heads()):
            _repair_skipped_migrations(
                cfg, script.get_bases()[0], engine, allow_repair=allow_repair, before_repair=approve
            )
        if plan.action != "fresh" and current != target:
            approve()
        command.upgrade(cfg, "head")
        _repair_skipped_migrations(
            cfg, script.get_bases()[0], engine, allow_repair=allow_repair, before_repair=approve
        )


def main(*, allow_repair: bool = False, plan_only: bool = False) -> None:
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    known = {rev.revision for rev in script.walk_revisions()}
    bases = script.get_bases()
    if len(script.get_heads()) != 1 or len(bases) != 1:
        raise SystemExit(
            f"The migration history is not a single line (heads: {script.get_heads()}, roots: {bases}). "
            "Fix it before starting."
        )

    safety = None if plan_only else MigrationSafety()
    engine = None
    try:
        engine = _engine()
        _wait_for_database(engine)
        with _migration_lock(engine) as lock_conn:
            inspector = inspect(lock_conn)
            tables = set(inspector.get_table_names())
            current: set[str] = set()
            if "alembic_version" in tables:
                current = {
                    row[0]
                    for row in lock_conn.execute(text("SELECT version_num FROM alembic_version"))
                }
            lock_conn.commit()

            _check_recorded_history(script, current, known)
            plan = decide(current, known, bool(tables - {"alembic_version"}))
            print(f"Database: {plan.action} ({plan.reason})", flush=True)
            if (
                plan.action == "adopt"
                and not allow_repair
                and _start_on_newer_database(engine, current, known)
            ):
                if not plan_only:
                    assert safety is not None
                    safety.record(
                        "newer_database_accepted",
                        source_revisions=sorted(current),
                        finished_at=int(time.time()),
                    )
                print("Database is newer than this build; nothing was changed.", flush=True)
                return
            if plan_only:
                print("Inspection only: no migration, stamp or replay was performed.", flush=True)
                if plan.action == "adopt":
                    _refuse_repair(plan.reason)
                if current == set(script.get_heads()):
                    _repair_skipped_migrations(cfg, bases[0], engine)
                return
            assert safety is not None
            _apply_plan(
                cfg,
                script,
                engine,
                lock_conn,
                current,
                plan=plan,
                allow_repair=allow_repair,
                safety=safety,
            )
            after = {
                row[0] for row in lock_conn.execute(text("SELECT version_num FROM alembic_version"))
            }
            lock_conn.commit()
            safety.record(
                "succeeded", resulting_revisions=sorted(after), finished_at=int(time.time())
            )
    except BaseException as error:
        if safety is not None:
            safety.failed(error)
        raise
    finally:
        if engine is not None:
            engine.dispose()
    print("Database is up to date.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument(
        "--plan", action="store_true", help="Inspect without changing the database."
    )
    operation.add_argument(
        "--repair-after-verified-backup",
        action="store_true",
        help="Operator confirms a restore-tested backup and successful clone rehearsal before replay.",
    )
    arguments = parser.parse_args()
    try:
        main(allow_repair=arguments.repair_after_verified_backup, plan_only=arguments.plan)
    # The CLI reports unexpected failures before exiting with a nonzero status.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # SQLAlchemy/driver exception text can contain parameters and connection secrets.
        ERROR_DETAIL = (
            f"Database operation failed ({type(exc).__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        print(f"Migration failed: {ERROR_DETAIL}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
