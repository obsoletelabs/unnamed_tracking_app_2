"""An additive single-head upgrade preserves manual events and refuses lossy rollback."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from src.core.config import settings
from src.database.models.calendar_source import CalendarSource, CalendarSourceEvent


def test_populated_upgrade_owner_constraints_cascade_and_guarded_downgrade():
    schema = "calendar_migration_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
    scoped = base.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)

    def migrate(*arguments, success=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
            cwd=Path(__file__).parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert (result.returncode == 0) is success, result.stderr
        return result

    with psycopg.connect(
        base.set(drivername="postgresql").render_as_string(hide_password=False), autocommit=True
    ) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        engine = create_engine(scoped)
        try:
            migrate("upgrade", "a4c7e19b52d8")
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            owner, other, manual = uuid4(), uuid4(), uuid4()
            for user in (owner, other):
                connection.execute(
                    "INSERT INTO users (id, username, email, password_hash, is_active, is_admin, created_at, updated_at) VALUES (%s, %s, %s, 'unused', true, false, 1, 1)",
                    (user, str(user), f"{user}@example.test"),
                )
            connection.execute(
                "INSERT INTO calendar_events (id, user_id, title, event_date, event_time, note, created_at, updated_at) VALUES (%s, %s, 'Manual event', '2026-10-19', '12:30', 'Keep me', 1, 1)",
                (manual, owner),
            )
            before = connection.execute("SELECT * FROM calendar_events").fetchall()
            migrate("upgrade", "head")
            migrate("upgrade", "head")
            assert connection.execute("SELECT * FROM calendar_events").fetchall() == before
            source_id = uuid4()
            with Session(engine) as db:
                db.add(
                    CalendarSource(
                        id=source_id,
                        user_id=owner,
                        name="External",
                        color="#123456",
                        hostname="example.test",
                        url_encrypted="encrypted",
                    )
                )
                db.commit()
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                connection.execute(
                    "INSERT INTO calendar_source_events (id, source_id, user_id, uid, recurrence_id, sequence, data, content_sha256, last_seen_at) VALUES (%s, %s, %s, 'a', '', 0, '{}', 'hash', 1)",
                    (uuid4(), source_id, other),
                )
            with Session(engine) as db:
                db.add(
                    CalendarSourceEvent(
                        source_id=source_id,
                        user_id=owner,
                        uid="a",
                        recurrence_id="",
                        sequence=0,
                        data={},
                        content_sha256="hash",
                        last_seen_at=1,
                    )
                )
                db.commit()
            blocked = migrate("downgrade", "a4c7e19b52d8", success=False)
            assert "calendar subscriptions and local overrides" in blocked.stderr
            connection.execute("DELETE FROM calendar_sources WHERE id = %s", (source_id,))
            assert connection.execute("SELECT count(*) FROM calendar_source_events").fetchone() == (
                0,
            )
            migrate("downgrade", "a4c7e19b52d8")
            assert connection.execute("SELECT * FROM calendar_events").fetchall() == before
            migrate("upgrade", "head")
            assert connection.execute("SELECT * FROM calendar_events").fetchall() == before
        finally:
            engine.dispose()
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
