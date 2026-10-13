from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import OperationalError

from src.database import migrate
from src.database.migrate import decide


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config("alembic.ini"))


def test_history_is_a_single_line():
    script = _script()
    assert len(script.get_heads()) == 1
    assert len(script.get_bases()) == 1


def test_database_startup_retries_transient_connection_failure(monkeypatch):
    engine = MagicMock()
    engine.connect.side_effect = [OperationalError("connect", {}, OSError("offline")), MagicMock()]
    delays = []
    monkeypatch.setattr(migrate.time, "sleep", delays.append)
    migrate._wait_for_database(engine)
    assert engine.connect.call_count == 2
    assert delays == [2]


def test_database_startup_reports_timeout(monkeypatch):
    engine = MagicMock()
    engine.connect.side_effect = OperationalError("connect", {}, OSError("offline"))
    times = iter([0, 61])
    monkeypatch.setattr(migrate.time, "time", lambda: next(times))
    with pytest.raises(SystemExit, match="Database is not reachable after 60s"):
        migrate._wait_for_database(engine)


def test_database_startup_does_not_retry_programming_errors(monkeypatch):
    engine = MagicMock()
    engine.connect.side_effect = TypeError("invalid engine configuration")
    delays = []
    monkeypatch.setattr(migrate.time, "sleep", delays.append)
    with pytest.raises(TypeError, match="invalid engine configuration"):
        migrate._wait_for_database(engine)
    assert delays == []


def test_migration_environment_accepts_url_encoded_database_credentials(monkeypatch):
    """Load the actual Alembic environment without connecting to a database."""
    from io import StringIO

    from alembic import command

    from src.core.config import settings

    url = "postgresql+psycopg://review%40host:pa%25ss%40word%2F%2B%3D@localhost/review"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    output = StringIO()
    cfg = Config("alembic.ini", output_buffer=output)
    command.ensure_version(cfg, sql=True)
    assert cfg.get_main_option("sqlalchemy.url") == url
    assert "CREATE TABLE alembic_version" in output.getvalue()


def test_empty_database_is_built_from_the_migrations():
    assert decide(set(), {"a"}, has_tables=False).action == "fresh"


def test_known_version_is_a_normal_upgrade():
    assert decide({"a"}, {"a", "b"}, has_tables=True).action == "upgrade"


def test_unknown_or_missing_version_is_adopted_not_crashed_on():
    assert decide({"gone"}, {"a"}, has_tables=True).action == "adopt"
    assert decide(set(), {"a"}, has_tables=True).action == "adopt"


def test_a_key_saved_in_settings_wins_over_the_environment(monkeypatch):
    from types import SimpleNamespace

    from src.core import integrations
    from src.core.crypto import encrypt_secret

    monkeypatch.setitem(integrations._ENV, "tmdb_api_key", "from-env")
    monkeypatch.setitem(integrations._ENV, "omdb_api_key", "omdb-env")
    monkeypatch.setitem(integrations._ENV, "tvdb_api_key", None)
    row = SimpleNamespace(
        igdb_client_id=None,
        igdb_client_secret=None,
        tvdb_api_key=None,
        omdb_api_key=None,
        tmdb_api_key=encrypt_secret("from-settings"),
    )
    keys = integrations.resolve_integrations(row)  # type: ignore[arg-type]
    assert keys.tmdb_api_key == "from-settings" and keys.sources["tmdb_api_key"] == "database"
    assert keys.omdb_api_key == "omdb-env" and keys.sources["omdb_api_key"] == "environment"
    assert keys.tvdb_api_key is None and "tvdb_api_key" not in keys.sources


def test_every_migration_after_the_baseline_is_safe_to_rerun():
    """migrate.py adopts a database from an older (squashed) history by
    attaching it to the baseline and running every later migration on it.
    Doing exactly that on the already-migrated test database must succeed and
    change nothing, or an existing install would fail to start."""
    from alembic import command
    from sqlalchemy import create_engine, text

    from src.core.config import settings

    cfg = Config("alembic.ini")
    script = _script()
    head = script.get_current_head()
    command.stamp(cfg, script.get_bases()[0], purge=True)
    try:
        command.upgrade(cfg, "head")
    finally:
        # never leave the shared test database detached from its real version
        command.stamp(cfg, head, purge=True)

    engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
    engine.dispose()


def test_replaying_steam_session_migration_preserves_connections_and_owner_binding():
    from uuid import uuid4

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, insert, select

    from src.core.config import settings
    from src.database.models.game import Game
    from src.database.models.user import User

    identity = uuid4()
    engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg"))
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                values = {
                    "steam_id": "76561198000000001",
                    "steam_library_owner_id": "76561198000000002",
                    "steam_access_token": "encrypted-access-fixture",
                    "steam_refresh_token": "encrypted-refresh-fixture",
                    "steam_token_expires_at": 2000000000,
                    "steam_login_state": "encrypted-pending-fixture",
                }
                conn.execute(
                    insert(User).values(
                        id=identity,
                        username=f"migration_{identity.hex}",
                        email=f"{identity}@example.test",
                        password_hash="fixture",
                        **values,
                    )
                )
                conn.execute(
                    insert(Game).values(
                        user_id=identity,
                        source="Steam",
                        external_id="1230410",
                        title="Migration fixture",
                        sort_title="migration fixture",
                        folder_location=f"migration-{identity.hex}",
                    )
                )
                migration = _script().get_revision("def2e465ec9f")
                with Operations.context(MigrationContext.configure(conn)):
                    migration.module.upgrade()
                    migration.module.upgrade()
                row = (
                    conn.execute(
                        select(*(getattr(User, name) for name in values)).where(User.id == identity)
                    )
                    .mappings()
                    .one()
                )
                assert dict(row) == values
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_integration_adoption_preserves_ongoing_grants_sources_and_saved_details():
    """Replay the actual revisions over populated new tables, then exercise rollback."""
    from uuid import uuid4

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, insert, select, text

    from src.core.config import settings
    from src.database.models.delegation import DelegatedGrant, DelegationRequest, IntegrationClient
    from src.database.models.game import Game
    from src.database.models.public_app import PublicAppActor, PublicAppSource
    from src.database.models.user import User

    engine = create_engine(settings.DATABASE_URL.replace("+asyncpg", "+psycopg"))
    user_id, client_id, request_id, grant_id, actor_id, game_id = [uuid4() for _ in range(6)]
    preset = {"min_players": 2, "max_players": 4, "crossplay": True}
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(
                    insert(User).values(
                        id=user_id,
                        username=user_id.hex,
                        email=f"{user_id}@example.test",
                        password_hash="fixture",
                    )
                )
                conn.execute(
                    insert(Game).values(
                        id=game_id,
                        user_id=user_id,
                        title="Together",
                        sort_title="together",
                        folder_location=game_id.hex,
                        play_compatibility=preset,
                    )
                )
                conn.execute(
                    insert(IntegrationClient).values(
                        id=client_id,
                        name="Existing client",
                        origin=f"https://{client_id.hex}.example.test",
                        enabled=True,
                    )
                )
                conn.execute(
                    insert(DelegationRequest).values(
                        id=request_id,
                        client_id=client_id,
                        requested_scopes=["games.read"],
                        code_challenge="a" * 64,
                        expires_at=2**40,
                    )
                )
                conn.execute(
                    insert(DelegatedGrant).values(
                        id=grant_id,
                        user_id=user_id,
                        client_id=client_id,
                        request_id=request_id,
                        scopes=["games.read"],
                        token_hash=grant_id.hex * 2,
                        expires_at=None,
                    )
                )
                conn.execute(
                    insert(PublicAppActor).values(
                        id=actor_id,
                        plugin_id="test.migration",
                        installation_id=uuid4(),
                        provider_installation_id=uuid4(),
                        subject="subject",
                        display_name="Existing actor",
                        expires_at=2**40,
                        avatar_url="https://example.test/avatar.png",
                    )
                )
                for kind in ("delegated", "legacy_api_key"):
                    conn.execute(
                        insert(PublicAppSource).values(
                            actor_id=actor_id,
                            origin=f"https://{kind}.example.test",
                            instance_id=kind,
                            credential_kind=kind,
                            request_id=request_id if kind == "delegated" else None,
                            credential="encrypted-fixture",
                            expires_at=None,
                            state="connected",
                            library=[{"title": "Existing library"}],
                        )
                    )
                script = _script()
                with Operations.context(MigrationContext.configure(conn)):
                    for revision in (
                        "150faa21e2c0",
                        "f28e1f724191",
                        "03402febb5ae",
                        "3acc697f5448",
                        "2f741285768c",
                        "da9e271523e2",
                    ):
                        script.get_revision(revision).module.upgrade()
                    assert conn.execute(
                        select(DelegatedGrant.expires_at).where(DelegatedGrant.id == grant_id)
                    ).one() == (None,)
                    assert (
                        conn.execute(
                            select(Game.play_compatibility).where(Game.id == game_id)
                        ).scalar_one()
                        == preset
                    )
                    sources = (
                        conn.execute(
                            select(PublicAppSource.library).where(
                                PublicAppSource.actor_id == actor_id
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert sources == [[{"title": "Existing library"}]] * 2
                    # Rollback forgets unsupported legacy credentials and expires
                    # ongoing access rather than inventing a new access duration.
                    script.get_revision("3acc697f5448").module.downgrade()
                    script.get_revision("03402febb5ae").module.downgrade()
                    expiry = conn.execute(
                        select(DelegatedGrant.expires_at).where(DelegatedGrant.id == grant_id)
                    ).scalar_one()
                    # BIGINT casts round numeric epoch fractions; an expiry must
                    # never round into the next second. Use this transaction's
                    # database clock so host clock drift cannot affect the test.
                    expected_expiry = int(
                        conn.execute(text("SELECT EXTRACT(EPOCH FROM now())")).scalar_one()
                    )
                    assert expiry == expected_expiry
                    remaining_sources = (
                        conn.execute(
                            select(PublicAppSource.expires_at).where(
                                PublicAppSource.actor_id == actor_id
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert remaining_sources == [expected_expiry]
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
