"""Optional display names stay presentation-only and never affect sign-in identity."""

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from src.api.routes import auth as auth_routes
from src.core.auth import (
    effective_display_name,
    get_current_actor,
    get_current_admin,
    get_current_user,
    hash_password,
    validate_display_name,
)
from src.core.config import settings
from src.database.models.auth import UserSession
from src.database.models.user import User
from src.database.session import get_db

PASSWORD = "Correct-horse-9!"


class SyncDb:
    """Adapt a SQLite session to the awaited calls these routes make."""

    def __init__(self, session):
        self.session = session

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    def add(self, row):
        self.session.add(row)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()

    async def refresh(self, row, **_kwargs):
        self.session.refresh(row)


@pytest.fixture
async def api(monkeypatch):
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    UserSession.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        admin = User(
            username="root.admin",
            email="root@example.test",
            password_hash=hash_password(PASSWORD),
            is_admin=True,
        )
        user = User(
            username="legacy@name",
            email="legacy@example.test",
            password_hash=hash_password(PASSWORD),
        )
        session.add_all([admin, user])
        session.commit()
        state = SimpleNamespace(current=user)
        actor = SimpleNamespace(credential_kind="api_key")
        app = FastAPI()
        app.include_router(auth_routes.router)
        app.dependency_overrides[get_db] = lambda: SyncDb(session)
        app.dependency_overrides[get_current_user] = lambda: state.current
        app.dependency_overrides[get_current_actor] = lambda: actor
        app.dependency_overrides[get_current_admin] = lambda: admin
        monkeypatch.setattr(auth_routes, "create_session", _fake_session)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield SimpleNamespace(http=http, session=session, user=user, admin=admin)
    engine.dispose()


async def _fake_session(_db, _user, _request):
    return SimpleNamespace(token="session-token")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ada Lovelace", "Ada Lovelace"),
        ("  Zoë   Ünal  ", "Zoë Ünal"),
        ("山田 太郎", "山田 太郎"),
        ("Player 🎮", "Player 🎮"),
        ("é", "é"),
        ("   ", None),
        ("", None),
        ('<b>x</b> & "y"', '<b>x</b> & "y"'),
        ("a" * 64, "a" * 64),
    ],
)
def test_validate_display_name_accepts_unicode_and_normalises(raw, expected):
    assert validate_display_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "line\nbreak",
        "tab\there",
        "nul\x00byte",
        "bell\x07",
        "esc\x1b[31m",
        "del\x7f",
        "rtl‮override",
        "iso⁦late",
        "zero​width",
        "bom﻿mark",
        "sep line",
        "nbsp space",
        "lone\ud800surrogate",
        "privateuse",
        "\nleading",
        "a" * 65,
    ],
)
def test_validate_display_name_rejects_control_invisible_and_overlong(raw):
    with pytest.raises(ValueError):
        validate_display_name(raw)


def test_effective_display_name_falls_back_to_username():
    assert effective_display_name(User(username="sign.in", display_name=None)) == "sign.in"
    assert effective_display_name(User(username="sign.in", display_name="")) == "sign.in"
    assert effective_display_name(User(username="sign.in", display_name="Nice Name")) == "Nice Name"


async def test_me_exposes_fallback_and_chosen_display_name(api, monkeypatch):
    async def no_picture(_user_id):
        return None

    monkeypatch.setattr(auth_routes, "profile_picture_version", no_picture)
    monkeypatch.setattr(auth_routes, "remember_app_url", no_picture)
    body = (await api.http.get("/api/auth/me")).json()
    assert (body["username"], body["display_name"], body["effective_display_name"]) == (
        "legacy@name",
        None,
        "legacy@name",
    )

    response = await api.http.patch("/api/auth/me", json={"display_name": "  Ada   Lovelace "})
    assert response.status_code == 200
    body = response.json()
    assert (body["username"], body["display_name"], body["effective_display_name"]) == (
        "legacy@name",
        "Ada Lovelace",
        "Ada Lovelace",
    )
    assert api.user.display_name == "Ada Lovelace"


async def test_profile_update_keeps_display_name_unless_provided_and_blank_clears(api, monkeypatch):
    async def no_picture(_user_id):
        return None

    monkeypatch.setattr(auth_routes, "profile_picture_version", no_picture)
    api.user.display_name = "Keep Me"
    api.session.commit()
    await api.http.patch("/api/auth/me", json={"email": "new@example.test"})
    assert api.user.display_name == "Keep Me"
    assert (await api.http.patch("/api/auth/me", json={"display_name": "  "})).status_code == 200
    assert api.user.display_name is None
    api.user.display_name = "Again"
    await api.http.patch("/api/auth/me", json={"display_name": None})
    assert api.user.display_name is None


@pytest.mark.parametrize("name", ["bad\nname", "spoof‮name", "x" * 65, "x" * 201])
async def test_profile_update_rejects_unsafe_display_names(api, name):
    response = await api.http.patch("/api/auth/me", json={"display_name": name})
    assert response.status_code == 422
    assert api.user.display_name is None


async def test_display_name_does_not_change_sign_in_identity(api):
    original_hash = api.user.password_hash
    api.user.display_name = "root.admin"
    api.session.commit()

    async def login(identifier):
        return await api.http.post(
            "/api/auth/login", json={"username_or_email": identifier, "password": PASSWORD}
        )

    assert (await login("legacy@name")).status_code == 200
    assert (await login("legacy@example.test")).status_code == 200
    # The display name is not a credential, even when it equals another username.
    assert (await login("root.admin")).json()["user_id"] == str(api.admin.id)
    api.user.display_name = "Only Display"
    api.session.commit()
    assert (await login("Only Display")).status_code == 401
    assert api.user.username == "legacy@name"
    assert api.user.password_hash == original_hash


async def test_duplicate_display_names_are_allowed(api):
    api.admin.display_name = "Same Name"
    api.session.commit()
    response = await api.http.patch("/api/auth/me", json={"display_name": "Same Name"})
    assert response.status_code == 200
    assert api.user.display_name == api.admin.display_name == "Same Name"


async def test_admin_user_list_and_create_keep_username_primary(api):
    api.user.display_name = "Pretty Name"
    api.session.commit()
    rows = {row["username"]: row for row in (await api.http.get("/api/auth/users")).json()}
    assert rows["legacy@name"]["display_name"] == "Pretty Name"
    assert rows["root.admin"]["display_name"] is None

    created = await api.http.post(
        "/api/auth/users",
        json={
            "username": "new.user",
            "display_name": "Nuovo Utente",
            "email": "new@example.test",
            "password": PASSWORD,
        },
    )
    assert created.status_code == 201
    assert (created.json()["username"], created.json()["display_name"]) == (
        "new.user",
        "Nuovo Utente",
    )
    plain = await api.http.post(
        "/api/auth/users",
        json={"username": "plain", "email": "plain@example.test", "password": PASSWORD},
    )
    assert plain.json()["display_name"] is None
    bad = await api.http.post(
        "/api/auth/users",
        json={
            "username": "bad",
            "display_name": "x​y",
            "email": "bad@example.test",
            "password": PASSWORD,
        },
    )
    assert bad.status_code == 422


def test_display_name_migration_is_additive_and_reversible():
    schema = "display_name_migration_" + uuid4().hex
    base = make_url(settings.DATABASE_URL)
    connection_url = base.set(drivername="postgresql").render_as_string(hide_password=False)
    scoped = base.update_query_dict({"options": f"-csearch_path={schema}"})
    import os
    import subprocess
    import sys

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

    with psycopg.connect(connection_url, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
        try:
            migrate("upgrade", "e4a0157eef4b")
            connection.execute(f'SET search_path TO "{schema}"')
            user_id = uuid4()
            connection.execute(
                "INSERT INTO users (id, username, email, password_hash, is_active, is_admin, "
                "created_at, updated_at) VALUES (%s, 'Old Name@x', 'old@example.test', 'hash', "
                "true, false, 1, 1)",
                (user_id,),
            )
            migrate("upgrade", "head")
            row = connection.execute(
                "SELECT username, display_name, email FROM users WHERE id=%s", (user_id,)
            ).fetchone()
            assert row == ("Old Name@x", None, "old@example.test")
            connection.execute("UPDATE users SET display_name='Kept' WHERE id=%s", (user_id,))
            migrate("downgrade", "e4a0157eef4b")
            columns = {
                name
                for (name,) in connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name='users'",
                    (schema,),
                )
            }
            assert "display_name" not in columns
            assert connection.execute(
                "SELECT username FROM users WHERE id=%s", (user_id,)
            ).fetchone() == ("Old Name@x",)
            migrate("upgrade", "head")
        finally:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_display_name_column_matches_model_length():
    assert re.fullmatch(r"VARCHAR\(100\)", str(User.__table__.c.display_name.type))
