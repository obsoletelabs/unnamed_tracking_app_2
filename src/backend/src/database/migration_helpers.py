"""Create-if-missing wrappers for Alembic operations.

Every migration after the baseline must be safe to run on a database that
already has part of what it adds: src/database/migrate.py attaches a
database from an older (squashed) history to the baseline and then runs
every later migration on it. These helpers look before they act, so the
same migration builds a fresh database and leaves an adopted one alone.
"""

from __future__ import annotations

import sqlalchemy as sa

# alembic.op is a proxy filled in while a migration runs, so pylint can't
# see its members
from alembic import op


def _inspector() -> sa.engine.Inspector:
    # a fresh inspector per call: Inspector caches what it has read, and
    # the schema changes between calls inside one migration
    # pylint: disable-next=no-member
    return sa.inspect(op.get_bind())


def has_table(table: str) -> bool:
    """Whether the table exists."""
    return _inspector().has_table(table)


def has_column(table: str, column: str) -> bool:
    """Whether the table exists and has the column."""
    if not has_table(table):
        return False
    return any(col["name"] == column for col in _inspector().get_columns(table))


def has_index(table: str, name: str) -> bool:
    """Whether the table exists and has an index with this name."""
    if not has_table(table):
        return False
    return any(index["name"] == name for index in _inspector().get_indexes(table))


def has_constraint(table: str, name: str) -> bool:
    """Whether the table exists and has a constraint with this name.

    The table is resolved through the search_path, exactly as the op.* call
    that follows resolves it. A constraint with the same name on another
    table, or in another schema, does not count."""
    # pylint: disable-next=no-member
    row = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = :name AND conrelid = to_regclass(quote_ident(:table))"
        ),
        {"name": name, "table": table},
    )
    return row.first() is not None


def create_table_if_missing(table: str, *columns: sa.schema.SchemaItem, **kw: object) -> None:
    """op.create_table, skipped when the table already exists."""
    if not has_table(table):
        # pylint: disable-next=no-member
        op.create_table(table, *columns, **kw)


def add_column_if_missing(table: str, column: sa.Column) -> None:
    """op.add_column, skipped when the column already exists."""
    if not has_column(table, column.name):
        # pylint: disable-next=no-member
        op.add_column(table, column)


def create_index_if_missing(
    name: str,
    table: str,
    columns: list[str],
    *,
    unique: bool = False,
    postgresql_where: sa.TextClause | None = None,
) -> None:
    """op.create_index, skipped when an index with this name exists."""
    if not has_index(table, name):
        # pylint: disable-next=no-member
        op.create_index(name, table, columns, unique=unique, postgresql_where=postgresql_where)


def drop_index_if_exists(name: str, table: str) -> None:
    """op.drop_index, skipped when there is no such index."""
    if has_index(table, name):
        # pylint: disable-next=no-member
        op.drop_index(name, table_name=table)


def create_unique_constraint_if_missing(name: str, table: str, columns: list[str]) -> None:
    """op.create_unique_constraint, skipped when the table already has a
    constraint with this name."""
    if not has_constraint(table, name):
        # pylint: disable-next=no-member
        op.create_unique_constraint(name, table, columns)


def delete_duplicate_episodes(table: str) -> None:
    """Keep one row per (season_id, episode_number), preferring the one with
    the most progress, so a unique constraint on the pair can be created on
    a database where a sync once raced."""
    # pylint: disable-next=no-member
    op.execute(
        f"""
        DELETE FROM {table} WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY season_id, episode_number
                    ORDER BY watched DESC, (note IS NOT NULL) DESC, (rating IS NOT NULL) DESC, id
                ) AS rn FROM {table}
            ) ranked WHERE rn > 1
        )
        """
    )


def refuse_if_populated(table: str, what: str, where: str = "true") -> None:
    """Abort a downgrade that would silently destroy retained notification history."""
    if not has_table(table):
        return
    # Identifiers and predicates are literals supplied by migrations, never user input.
    if op.get_bind().scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE {where})")):  # noqa: S608
        raise RuntimeError(
            f"Refusing to drop populated {what}; retain the schema or remove the rows "
            "explicitly (within retention rules) before downgrading."
        )
