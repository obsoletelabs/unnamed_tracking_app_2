"""Select an explicit test database before application imports and isolate test storage."""

import os

import pytest

# Compose's database components and either discovered .env must never override
# the explicitly selected test URL. This runs before test collection imports src.
_test_database_url = (
    os.environ.get("TEST_DATABASE_URL", "").strip() or os.environ.get("DATABASE_URL", "").strip()
)
if not _test_database_url:
    raise pytest.UsageError(
        "Set TEST_DATABASE_URL or DATABASE_URL to a dedicated, migrated test database. "
        "The backend tests do not load the development .env."
    )
os.environ["DATABASE_URL"] = _test_database_url
os.environ["ENV_FILE"] = os.devnull
for _component in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
    os.environ.pop(_component, None)
# Importing settings must not generate or persist a deployment key during collection.
os.environ.setdefault("SECRET_KEY", "XJVqc8r7Uj801VzGUYhlQV8A2tZTBepjOYEiP2pTzFk=")


@pytest.fixture(autouse=True)
def isolated_plugin_manager_state(monkeypatch, tmp_path):
    monkeypatch.setenv("PLUGIN_MANAGER_STATE_PATH", str(tmp_path / "plugin-manager.json"))


@pytest.fixture(autouse=True)
def isolated_game_asset_root(monkeypatch, tmp_path):
    """Keep game-folder tests out of the host's real /data filesystem."""
    from src.helpers import save_game_asset

    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path / "users")


@pytest.fixture(autouse=True)
async def isolated_auth_quota_rows(monkeypatch):
    """Keep real enforcement active while isolating its shared counters between tests."""
    from src.database.auth_limits import auth_limit_engine
    from src.database.models.plugin_auth_flow import AuthRequestLimit
    from src.database.session import SessionLocal
    from src.features import auth_rate_limits

    keys: set[str] = set()
    original = auth_rate_limits.limit_key

    def tracked_key(purpose: str, limit: auth_rate_limits.AuthLimit, now: int) -> str:
        key = original(purpose, limit, now)
        keys.add(key)
        return key

    monkeypatch.setattr(auth_rate_limits, "limit_key", tracked_key)
    yield
    if keys:
        # Core SQL avoids forcing unrelated partially imported ORM mappers to initialize.
        table = AuthRequestLimit.__table__
        async with SessionLocal() as db:
            await db.execute(table.delete().where(table.c.key.in_(keys)))
            await db.commit()
    # pytest creates a fresh event loop for each async test. The production pool is
    # worker-local; recreate its asyncio queue before another test loop can wait on it.
    await auth_limit_engine.dispose()
