"""Widen provider IDs without touching other tables or truncating on rollback."""

import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from src.database.session import SessionLocal


async def test_identity_migration_preserves_values_and_refuses_lossy_downgrade(monkeypatch):
    migration = importlib.import_module(
        "src.database.migrations.versions.c696194f78fd_widen_game_provider_identities"
    )
    async with SessionLocal() as db:
        # PostgreSQL resolves this transaction-local temporary table before the
        # application table. No real game or application migration state is touched.
        await db.execute(
            text("CREATE TEMP TABLE games (id integer PRIMARY KEY, external_id varchar(64))")
        )
        await db.execute(
            text("INSERT INTO games VALUES (1, :value), (2, NULL)"), {"value": "x" * 64}
        )
        connection = await db.connection()

        def upgrade(sync_connection):
            monkeypatch.setattr(
                migration, "op", Operations(MigrationContext.configure(sync_connection))
            )
            migration.upgrade()

        def downgrade(sync_connection):
            monkeypatch.setattr(
                migration, "op", Operations(MigrationContext.configure(sync_connection))
            )
            migration.downgrade()

        await connection.run_sync(upgrade)
        assert (
            await db.execute(text("SELECT external_id FROM games ORDER BY id"))
        ).scalars().all() == ["x" * 64, None]
        await db.execute(text("INSERT INTO games VALUES (3, :value)"), {"value": "y" * 256})
        with pytest.raises(RuntimeError, match="Cannot narrow game identities"):
            await connection.run_sync(downgrade)
        assert await db.scalar(text("SELECT external_id FROM games WHERE id=3")) == "y" * 256
        await db.execute(text("DELETE FROM games WHERE id=3"))
        await connection.run_sync(downgrade)
        assert (
            await db.execute(text("SELECT external_id FROM games ORDER BY id"))
        ).scalars().all() == ["x" * 64, None]
        await connection.run_sync(upgrade)
        await db.execute(text("INSERT INTO games VALUES (3, :value)"), {"value": "y" * 256})
        await db.rollback()
