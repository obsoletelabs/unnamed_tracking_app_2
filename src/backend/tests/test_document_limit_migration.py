"""The documents upload cap migration keeps every existing limit override."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings

PREVIOUS_HEAD = "c10098e58426"
COLUMNS = "max_upload_size_mb, max_clip_size_mb, max_world_save_size_mb, branding_name"


def test_document_limit_upgrade_replay_and_downgrade_keep_existing_overrides():
    schema = "doc_limit_migration_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
    url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    scoped = base.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)

    def migrate(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
            cwd=Path(__file__).parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def overrides(connection):
        return connection.execute(
            sql.SQL("SELECT {} FROM app_integration_settings").format(sql.SQL(COLUMNS))
        ).fetchone()

    def has_document_limit(connection):
        return connection.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = %s"
            " AND table_name = 'app_integration_settings' AND column_name = 'max_doc_size_mb'",
            (schema,),
        ).fetchone() == (1,)

    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", PREVIOUS_HEAD)
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            # A deployment that already saved limits and branding before docs had a cap.
            connection.execute(
                sql.SQL(
                    "INSERT INTO app_integration_settings (id, {}, updated_at)"
                    " VALUES (%s, 40, 300, 900, 'Existing', 1)"
                ).format(sql.SQL(COLUMNS)),
                (uuid4(),),
            )
            existing = overrides(connection)

            migrate("upgrade", "head")
            assert overrides(connection) == existing
            # No override: docs start on the MAX_DOC_SIZE_MB environment default.
            assert connection.execute(
                "SELECT max_doc_size_mb FROM app_integration_settings"
            ).fetchone() == (None,)
            connection.execute("UPDATE app_integration_settings SET max_doc_size_mb = 64")

            # Replaying the revision on a database that already has the column is harmless.
            migrate("stamp", PREVIOUS_HEAD)
            migrate("upgrade", "head")
            assert connection.execute(
                "SELECT max_doc_size_mb FROM app_integration_settings"
            ).fetchone() == (64,)

            migrate("downgrade", PREVIOUS_HEAD)
            assert not has_document_limit(connection)
            assert overrides(connection) == existing
            migrate("upgrade", "head")
            assert has_document_limit(connection)
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
