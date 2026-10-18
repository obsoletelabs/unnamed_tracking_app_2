"""Brings the database up to date on every start, so updating the app is
just pulling the new version and starting it.

What it does, in order:
1. Waits for the database to accept connections (and gives up with a clear
   message after a minute, instead of retrying a real error 30 times).
2. Takes a lock so two starting containers never migrate at once.
3. Looks at what the database says its version is:
   - empty database: builds everything from the migrations
   - a version this code has never heard of (the migration history was
     rewritten or squashed since this database was created), or tables
     with no version at all: it is attached to the starting migration and
     brought up to date, keeping all data. Afterwards it is checked against
     the models, and if something the code needs is STILL missing (nothing
     in the history creates it) it stops and says exactly what, instead of
     guessing, and forgets the attachment so the next start checks again.
   - a version that is in the history: nothing special
4. Runs every migration newer than the database.

Rewriting the migration history is therefore safe for existing installs:
any database from the old history is adopted automatically on its next
start. New migrations should still be written to be safe to run twice
(create-if-missing), see docs/migrations.md."""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
import time
from dataclasses import dataclass

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.database.base import Base

_LOCK_ID = 7_310_442_901
_WAIT_SECONDS = 60


@dataclass(frozen=True)
class Plan:
    action: str  # "fresh" | "upgrade" | "adopt"
    reason: str


def decide(current: set[str], known: set[str], has_tables: bool) -> Plan:
    """What to do with a database, from its recorded versions, the versions
    this code's history contains, and whether it has any tables at all."""
    if not has_tables:
        return Plan("fresh", "empty database")
    if not current:
        return Plan("adopt", "tables exist but no recorded version")
    unknown = sorted(current - known)
    if unknown:
        return Plan(
            "adopt", f"recorded version {', '.join(unknown)} is not in this migration history"
        )
    return Plan("upgrade", "recorded version is in the history")


def _register_models() -> None:
    from src.database import models  # pylint: disable=import-outside-toplevel

    for module in pkgutil.walk_packages(models.__path__, models.__name__ + "."):
        importlib.import_module(module.name)


def missing_from_database(engine: Engine) -> list[str]:
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
        with engine.connect() as conn:
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

    return create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg"), pool_pre_ping=True)


# PostgreSQL answered and refused the connection: waiting will not change the answer.
_CONFIGURATION_ERRORS = (
    "password authentication failed",
    "no pg_hba.conf entry",
    "does not exist",  # role "x" / database "x" does not exist
)


def configuration_error(exc: BaseException) -> str | None:
    """A recovery message when the database refused the connection because of the
    configured credentials or name, or None when the failure may be temporary."""
    detail = str(exc).lower()
    if any(marker in detail for marker in _CONFIGURATION_ERRORS):
        return (
            "The database refused the configured credentials or database name. Check "
            "POSTGRES_USER, POSTGRES_PASSWORD and POSTGRES_DB (or DATABASE_URL) against the "
            "database server; waiting will not fix this."
        )
    return None


def _wait_for_database(engine: Engine) -> None:
    deadline = time.time() + _WAIT_SECONDS
    last = ""
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except (SQLAlchemyError, OSError) as exc:
            last = str(exc).splitlines()[0]
            refused = configuration_error(exc)
            if refused:
                raise SystemExit(f"Database configuration error: {last}. {refused}") from exc
            if time.time() > deadline:
                raise SystemExit(
                    f"Database is not reachable after {_WAIT_SECONDS}s: {last}. This is usually "
                    "temporary: check that the database is running and that POSTGRES_HOST and "
                    "POSTGRES_PORT point at it, then restart."
                ) from exc
            print(f"Waiting for the database ({last})...", flush=True)
            time.sleep(2)


def _reattach(cfg: Config, base: str, engine: Engine) -> None:
    """Attach the database to the starting migration and replay every newer one.

    The migrations after the starting one are written to be safe on a database that
    already has part of what they add, so attach first and check afterwards: checking
    the models first would refuse every database that predates a migration this
    version ships."""
    print(f"Attaching the database to migration {base}. Its data is untouched.", flush=True)
    command.stamp(cfg, base, purge=True)
    command.upgrade(cfg, "head")
    problems = missing_from_database(engine)
    if problems:
        command.stamp(cfg, "base", purge=True)  # not attached: check again next start
        raise SystemExit(
            "This database was made by an older version and is missing what this version needs, "
            "and no migration creates it: "
            + ", ".join(problems)
            + ". Start the previous version once so it can finish its own updates, then update again."
        )


def _repair_skipped_migrations(cfg: Config, base: str, engine: Engine) -> None:
    """Replay the history when a recorded version claims migrations that never ran.

    A database stamped by an image whose history placed a revision on a different
    parent (for example a pre-merge branch build) records a version that main also
    knows, so Alembic believes every ancestor already ran. Checking the schema after
    the normal upgrade catches the columns and tables those skipped ancestors add."""
    problems = missing_from_database(engine)
    if not problems:
        return
    print(
        "The recorded database version is known, but "
        + ", ".join(problems)
        + " is missing, so earlier migrations were skipped. Replaying the migration history.",
        flush=True,
    )
    _reattach(cfg, base, engine)


def main() -> None:
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    known = {rev.revision for rev in script.walk_revisions()}
    bases = script.get_bases()
    if len(script.get_heads()) != 1 or len(bases) != 1:
        raise SystemExit(
            f"The migration history is not a single line (heads: {script.get_heads()}, roots: {bases}). "
            "Fix it before starting."
        )

    engine = _engine()
    _wait_for_database(engine)

    with engine.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": _LOCK_ID})
        try:
            inspector = inspect(lock_conn)
            tables = set(inspector.get_table_names())
            has_tables = bool(tables - {"alembic_version"})
            current: set[str] = set()
            if "alembic_version" in tables:
                current = {
                    row[0]
                    for row in lock_conn.execute(text("SELECT version_num FROM alembic_version"))
                }
            lock_conn.commit()

            plan = decide(current, known, has_tables)
            print(f"Database: {plan.action} ({plan.reason})", flush=True)

            if plan.action == "adopt":
                _reattach(cfg, bases[0], engine)
            else:
                command.upgrade(cfg, "head")
                if plan.action == "upgrade":
                    _repair_skipped_migrations(cfg, bases[0], engine)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _LOCK_ID})
            lock_conn.commit()
    print("Database is up to date.", flush=True)


def _report_failure(exc: Exception) -> None:
    first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    print(
        f"Migration failed ({type(exc).__name__}): {first_line}. The full error is in the "
        "migration log. Fix the cause, for example permissions of the database user to "
        "create tables, and restart; the migration is safe to run again.",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    # The CLI reports unexpected failures before exiting with a nonzero status.
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _report_failure(exc)
        raise SystemExit(1) from exc
