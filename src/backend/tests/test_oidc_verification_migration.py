"""Real upgrades and adopted schemas preserve existing OIDC configuration."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings


def test_oidc_verification_upgrade_replay_and_downgrade_preserve_provider():
    schema = "oidc_migration_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
    connection_url = base.set(drivername="postgresql").render_as_string(hide_password=False)
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
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    with psycopg.connect(connection_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            migrate("upgrade", "83bb783fd35e")
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            provider_id = uuid4()
            connection.execute(
                "INSERT INTO oidc_settings (id, enabled, client_id, scopes, groups_claim, "
                "user_match_field, default_login_method, login_button_text, allow_new_users, "
                "updated_at) VALUES (%s, true, 'existing-client', 'openid profile email', "
                "'groups', 'email', 'local', 'Continue with SSO', true, 1)",
                (provider_id,),
            )
            migrate("upgrade", "head")
            statement = "SELECT client_id, require_verified_email FROM oidc_settings WHERE id=%s"
            assert connection.execute(statement, (provider_id,)).fetchone() == (
                "existing-client",
                False,
            )
            connection.execute(
                "UPDATE oidc_settings SET require_verified_email=true WHERE id=%s", (provider_id,)
            )
            migrate("stamp", "83bb783fd35e")
            migrate("upgrade", "head")
            assert connection.execute(statement, (provider_id,)).fetchone() == (
                "existing-client",
                True,
            )
            migrate("downgrade", "83bb783fd35e")
            assert connection.execute(
                "SELECT client_id FROM oidc_settings WHERE id=%s", (provider_id,)
            ).fetchone() == ("existing-client",)
            migrate("upgrade", "head")
            assert connection.execute(statement, (provider_id,)).fetchone() == (
                "existing-client",
                False,
            )
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
