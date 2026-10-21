"""Upgrade populated databases from historical schema revisions to the current head.

Each case builds an isolated PostgreSQL schema (never the shared tables of the test database),
migrates it to an older revision, fills every table with synthetic rows, upgrades to head and
compares domain-level invariants: every row that existed is still there, owned by the same
user, with the same values in every column that survived, foreign keys remain valid and files
on disk are byte-identical. Only table names, column names and counts are ever reported, so a
failure cannot leak row contents.

The starting revisions are the published points of the linear and divergent histories the
project has shipped: the initial schema, the first plugin revisions, the tip of a divergent
branch that was later reconciled, reconciliation merges, the pre-identity baseline, the merge
after the game identity model and the revision before the current head.
"""

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings

BACKEND = Path(__file__).parents[1]
HEAD = "head"

# (revision, why it is a meaningful starting point)
STARTS = [
    ("f6bb4f914739", "initial schema"),
    ("7b2d4a9e8c11", "early release before plugin permissions"),
    ("a97470898855", "tip of a divergent branch later joined by a reconciliation merge"),
    ("b57b38daf5b5", "published reconciliation of three divergent lines"),
    ("c10098e58426", "baseline before the identity model"),
    ("50b007897f73", "merge after the game identity model and display names"),
    ("a4c7e19b52d8", "revision before the current head"),
]

# Intentional transformations. A (table, column) listed here may change value during an
# upgrade; a table listed in DROPPED_TABLES may disappear. Anything else that differs fails.
TRANSFORMED_COLUMNS: dict[tuple[str, str], str] = {
    # 22ff87d4d01c maps the legacy delivery vocabulary onto the notification core's states
    # (failed_permanent -> failed, retry_wait/processing -> retry, suppressed/cancelled ->
    # skipped); values outside that vocabulary are normalised the same way.
    ("notification_deliveries", "status"): "notification core delivery states",
}
DROPPED_TABLES: dict[str, str] = {}

# Tables that must be populated at every starting point they exist, so the comparison cannot
# pass vacuously.
CORE_TABLES = {"users", "games"}
MINIMUM_POPULATED_SHARE = 0.6
USER_ROWS = 3


def _urls(schema: str) -> tuple[str, dict[str, str]]:
    base = make_url(settings.DATABASE_URL)
    admin = base.set(drivername="postgresql").render_as_string(hide_password=False)
    scoped = base.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)
    return admin, environment


def _alembic(environment: dict[str, str], *arguments: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=BACKEND,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    # Alembic output holds revision names and SQL error text, never row data.
    assert result.returncode == 0, f"alembic {' '.join(arguments)} failed:\n{result.stderr[-2000:]}"
    return result.stdout + result.stderr


# --------------------------------------------------------------------- populating ---


def _schema_model(connection: psycopg.Connection, schema: str):
    columns: dict[str, list[dict]] = {}
    for table, name, data_type, udt, nullable, default, length in connection.execute(
        "SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default,"
        " character_maximum_length FROM information_schema.columns WHERE table_schema = %s"
        " AND table_name <> 'alembic_version' ORDER BY table_name, ordinal_position",
        (schema,),
    ):
        columns.setdefault(table, []).append(
            {
                "name": name,
                "type": data_type,
                "udt": udt,
                "required": nullable == "NO" and default is None,
                "length": length,
            }
        )
    foreign: dict[str, dict[str, tuple[str, str]]] = {}
    for table, column, parent, parent_column in connection.execute(
        "SELECT child.relname, child_column.attname, parent.relname, parent_column.attname"
        " FROM pg_constraint c"
        " CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS key(child_number, parent_number)"
        " JOIN pg_class child ON child.oid = c.conrelid"
        " JOIN pg_class parent ON parent.oid = c.confrelid"
        " JOIN pg_attribute child_column ON child_column.attrelid = c.conrelid"
        "   AND child_column.attnum = key.child_number"
        " JOIN pg_attribute parent_column ON parent_column.attrelid = c.confrelid"
        "   AND parent_column.attnum = key.parent_number"
        " WHERE c.contype = 'f' AND c.connamespace = %s::regnamespace",
        (schema,),
    ):
        foreign.setdefault(table, {}).setdefault(column, (parent, parent_column))
    return columns, foreign


def _order(columns: dict, foreign: dict) -> list[str]:
    """Parents before children; tables in a dependency cycle are left for last and skipped."""
    ordered: list[str] = []
    pending = set(columns)
    while pending:
        ready = sorted(
            table
            for table in pending
            if all(
                parent == table or parent not in pending
                for parent, _ in foreign.get(table, {}).values()
            )
        )
        if not ready:
            break
        ordered.extend(ready)
        pending.difference_update(ready)
    return ordered


def _value(connection, table: str, column: dict, index: int, now: datetime):
    kind, name = column["type"], column["name"]
    if kind == "uuid":
        return uuid5(NAMESPACE_URL, f"{table}.{name}.{index}")
    if kind in {"character varying", "text", "character"}:
        text = f"{table}.{name}.{index}"
        if name == "email":
            text = f"user{index}@example.test"
        return text[: column["length"]] if column["length"] else text
    if kind in {"integer", "bigint", "smallint", "numeric", "real", "double precision"}:
        return 1 + index
    if kind == "boolean":
        return False
    if kind in {"json", "jsonb"}:
        return "{}"
    if kind.startswith("timestamp"):
        return now
    if kind == "date":
        return date(2024, 1, 1)
    if kind == "bytea":
        return b"x"
    if kind == "ARRAY":
        return "{}"
    if kind == "USER-DEFINED":
        row = connection.execute(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid"
            " WHERE t.typname = %s ORDER BY e.enumsortorder LIMIT 1",
            (column["udt"],),
        ).fetchone()
        return row[0] if row else None
    return None


def _populate(connection: psycopg.Connection, schema: str) -> dict[str, int]:
    """Insert synthetic rows into every table that can take one; return rows per table."""
    columns, foreign = _schema_model(connection, schema)
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    inserted: dict[str, int] = {}
    first_rows: dict[str, dict] = {}
    for table in _order(columns, foreign):
        wanted = USER_ROWS if table == "users" else 1
        for index in range(wanted):
            row: dict = {}
            usable = True
            for column in columns[table]:
                if not column["required"]:
                    continue
                parent = foreign.get(table, {}).get(column["name"])
                if parent is not None:
                    source = first_rows.get(parent[0])
                    if source is None and parent[0] != table:
                        usable = False
                        break
                    row[column["name"]] = (source or {}).get(parent[1])
                    continue
                value = _value(connection, table, column, index, now)
                if value is None:
                    usable = False
                    break
                row[column["name"]] = value
            if not usable:
                break
            names = list(row)
            statement = (
                sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(map(sql.Identifier, names)),
                    sql.SQL(", ").join(sql.Placeholder() for _ in names),
                )
                if names
                else sql.SQL("INSERT INTO {} DEFAULT VALUES").format(sql.Identifier(table))
            )
            try:
                connection.execute(statement, [row[name] for name in names])
            except psycopg.Error:
                # A check or exclusion rule these generic values do not satisfy; the table is
                # reported as unpopulated and the populated share is asserted below.
                break
            inserted[table] = inserted.get(table, 0) + 1
            first_rows.setdefault(table, row)
    return inserted


# ----------------------------------------------------------------------- comparing ---


def _snapshot(connection: psycopg.Connection, schema: str) -> dict[str, dict]:
    """Rows of every table as JSON, keyed by primary key (or the whole row when there is none)."""
    snapshot: dict[str, dict] = {}
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s"
            " AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version'",
            (schema,),
        )
    ]
    for table in tables:
        key_columns = [
            row[0]
            for row in connection.execute(
                "SELECT a.attname FROM pg_index i JOIN pg_attribute a"
                " ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
                " WHERE i.indisprimary AND i.indrelid = %s::regclass ORDER BY a.attnum",
                (f'"{schema}"."{table}"',),
            )
        ]
        rows = [
            row[0]
            for row in connection.execute(
                sql.SQL("SELECT to_jsonb(t) FROM {} t").format(sql.Identifier(table))
            )
        ]
        snapshot[table] = {
            (
                json.dumps([row[column] for column in key_columns])
                if key_columns
                else json.dumps(row, sort_keys=True)
            ): row
            for row in rows
        }
    return snapshot


def compare(before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    """Violated invariants as table and column names only (never values)."""
    problems: list[str] = []
    for table, rows in before.items():
        if table not in after:
            if table not in DROPPED_TABLES:
                problems.append(f"table {table} disappeared with {len(rows)} row(s)")
            continue
        for key, row in rows.items():
            current = after[table].get(key)
            if current is None:
                problems.append(f"table {table} lost a row it had before the upgrade")
                continue
            for column, value in row.items():
                if column not in current:
                    continue  # a dropped column is a schema change, not a lost record
                if current[column] != value and (table, column) not in TRANSFORMED_COLUMNS:
                    problems.append(f"table {table} column {column} changed value")
    return sorted(set(problems))


def _checksums(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _foreign_keys_hold(connection: psycopg.Connection, schema: str) -> list[str]:
    """Orphans that a `NOT VALID` or dropped constraint could have let through."""
    columns, foreign = _schema_model(connection, schema)
    orphans = []
    for table, mapping in foreign.items():
        for column, (parent, parent_column) in mapping.items():
            count = connection.execute(
                sql.SQL(
                    "SELECT count(*) FROM {child} c WHERE c.{col} IS NOT NULL AND NOT EXISTS"
                    " (SELECT 1 FROM {parent} p WHERE p.{pcol} = c.{col})"
                ).format(
                    child=sql.Identifier(table),
                    col=sql.Identifier(column),
                    parent=sql.Identifier(parent),
                    pcol=sql.Identifier(parent_column),
                )
            ).fetchone()
            if count and count[0]:
                orphans.append(f"{table}.{column} references missing {parent} rows")
    return orphans


# ------------------------------------------------------------------------ the tests ---


class Upgrade:
    """One isolated schema, migrated to `start`, populated and ready to upgrade."""

    def __init__(self, start: str, tmp_path: Path):
        self.start = start
        self.schema = "migration_matrix_" + uuid4().hex
        self.admin_url, self.environment = _urls(self.schema)
        self.files = tmp_path / "data"
        self.files.mkdir()

    def run(self, steps: tuple[str, ...] = (HEAD,)) -> tuple[dict, dict, dict[str, int]]:
        with psycopg.connect(self.admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            try:
                _alembic(self.environment, "upgrade", self.start)
                connection.execute(
                    sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema))
                )
                populated = _populate(connection, self.schema)
                for index in range(3):
                    (self.files / f"game-{index}.bin").write_bytes(bytes([index]) * 4096)
                before = _snapshot(connection, self.schema)
                self.file_hashes = _checksums(self.files)
                for step in steps:
                    _alembic(self.environment, "upgrade", step)
                after = _snapshot(connection, self.schema)
                self.orphans = _foreign_keys_hold(connection, self.schema)
                self.current = _alembic(self.environment, "current")
                self.connection_url = self.admin_url
            finally:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
                )
        return before, after, populated

    def drop(self) -> None:
        with psycopg.connect(self.admin_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema))
            )


@pytest.fixture
def isolated(tmp_path):
    made: list[Upgrade] = []

    def build(start: str) -> Upgrade:
        upgrade = Upgrade(start, tmp_path)
        made.append(upgrade)
        return upgrade

    yield build
    for upgrade in made:
        upgrade.drop()


def _assert_preserved(upgrade: Upgrade, before: dict, after: dict, populated: dict) -> None:
    assert CORE_TABLES <= set(populated) or not CORE_TABLES <= set(before), (
        "core tables could not be populated: " + ", ".join(sorted(CORE_TABLES - set(populated)))
    )
    share = len(populated) / max(1, len(before))
    assert share >= MINIMUM_POPULATED_SHARE, (
        f"only {len(populated)} of {len(before)} tables were populated at {upgrade.start}"
    )
    assert compare(before, after) == []
    assert upgrade.orphans == []
    assert _checksums(upgrade.files) == upgrade.file_hashes
    assert "(head)" in upgrade.current


@pytest.mark.parametrize(("start", "why"), STARTS, ids=[start for start, _ in STARTS])
def test_populated_database_upgrades_to_head_without_losing_records(isolated, start, why):
    upgrade = isolated(start)
    before, after, populated = upgrade.run()
    _assert_preserved(upgrade, before, after, populated)


def test_upgrade_in_two_stages_and_repeated_upgrade_change_nothing(isolated):
    """An upgrade interrupted after an intermediate revision resumes to the same result."""
    upgrade = isolated("f6bb4f914739")
    before, after, populated = upgrade.run(("c10098e58426", HEAD, HEAD))
    _assert_preserved(upgrade, before, after, populated)


def test_a_migration_that_loses_a_record_or_changes_ownership_fails_the_comparison():
    owner = "a"
    before = {
        "games": {
            '["1"]': {"id": "1", "user_id": owner, "title": "Kept"},
            '["2"]': {"id": "2", "user_id": owner, "title": "Lost"},
        },
        "notes": {'["1"]': {"id": "1", "game_id": "1"}},
    }
    intact = json.loads(json.dumps(before))
    assert compare(before, intact) == []

    lost = json.loads(json.dumps(before))
    del lost["games"]['["2"]']
    assert compare(before, lost) == ["table games lost a row it had before the upgrade"]

    stolen = json.loads(json.dumps(before))
    stolen["games"]['["1"]']["user_id"] = "b"
    assert compare(before, stolen) == ["table games column user_id changed value"]

    dropped = {key: value for key, value in intact.items() if key != "notes"}
    assert compare(before, dropped) == ["table notes disappeared with 1 row(s)"]

    widened = json.loads(json.dumps(before))
    widened["games"]['["1"]']["extra"] = "added column"
    assert compare(before, widened) == []


def test_comparison_reports_names_and_counts_but_never_values():
    before = {"users": {'["1"]': {"id": "1", "email": "private@example.test"}}}
    after = {"users": {'["1"]': {"id": "1", "email": "other@example.test"}}}
    problems = compare(before, after)
    assert problems == ["table users column email changed value"]
    assert "example.test" not in " ".join(problems)


def test_a_deleted_file_changes_the_checksums(tmp_path):
    (tmp_path / "save.bin").write_bytes(b"data")
    before = _checksums(tmp_path)
    (tmp_path / "save.bin").unlink()
    assert _checksums(tmp_path) != before
