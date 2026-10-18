"""Startup repairs a known-head database whose job-control ancestors never ran (#272)."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.database.models.job_setting import JobSetting


def test_startup_repairs_skipped_job_columns_and_preserves_schedules():
    schema = "skipped_jobs_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
    scoped = base.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)
    engine = create_engine(scoped.set(drivername="postgresql+psycopg"))
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    columns = script.get_revision("3a3fb6dccb3e").module.COLUMNS
    saved = {
        "job_id": "media_trash_cleanup",
        "enabled": True,
        "interval_minutes": 137,
        "last_run_at": 1728000000,
        "last_result": {"deleted": 3, "errors": []},
        "updated_at": 1728000001,
    }

    def startup():
        result = subprocess.run(
            [sys.executable, "-m", "src.database.migrate"],
            cwd=Path(__file__).parents[1],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            assert "Database: fresh" in startup()
            with engine.begin() as seed:
                seed.execute(insert(JobSetting).values(**saved))
            with connection.cursor() as cursor:
                for column in columns:
                    cursor.execute(
                        sql.SQL("ALTER TABLE {}.job_settings DROP COLUMN {}").format(
                            sql.Identifier(schema), sql.Identifier(column.name)
                        )
                    )

            def snapshot():
                with engine.connect() as check:
                    revision = check.exec_driver_sql(
                        "SELECT version_num FROM alembic_version"
                    ).all()
                    schedule = (
                        check.execute(select(*(getattr(JobSetting, key) for key in saved)))
                        .mappings()
                        .one()
                    )
                return revision, dict(schedule)

            before = snapshot()
            assert before == ([(script.get_current_head(),)], saved)
            assert "earlier migrations were skipped" in startup()
            assert snapshot() == before
            with engine.connect() as check:
                restored = check.execute(
                    select(*(getattr(JobSetting, column.name) for column in columns))
                ).one()
            assert tuple(restored) == (False, False, "idle", None, 0, None, None, None)

            again = startup()
            assert "Database: upgrade" in again
            assert "Replaying" not in again and "Attaching" not in again
            assert snapshot() == before
        finally:
            engine.dispose()
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
