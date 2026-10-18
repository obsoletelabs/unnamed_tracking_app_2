"""Exercise pytest's configuration bootstrap in a fresh interpreter (#278)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def run_probe(tmp_path, *, database_url=None, test_database_url=None, env_location="root"):
    root = tmp_path / "repository"
    backend = root / "src" / "backend"
    tests = backend / "tests"
    tests.mkdir(parents=True)
    shutil.copyfile(Path(__file__).with_name("conftest.py"), tests / "conftest.py")
    directory = root if env_location == "root" else backend
    (directory / ".env").write_text(
        "POSTGRES_USER=development\nPOSTGRES_PASSWORD=fixture\n"
        "POSTGRES_DB=development\nPOSTGRES_HOST=development-db\n",
        encoding="utf-8",
    )
    (tests / "test_probe.py").write_text(
        "from sqlalchemy.engine import make_url\n"
        "from src.core.config import settings\n"
        "def test_uses_only_the_selected_test_database():\n"
        "    url = make_url(settings.DATABASE_URL)\n"
        "    assert (url.host, url.database) == ('isolated-db', 'test_only')\n"
        "    assert settings.POSTGRES_USER is None\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    for name in ("DATABASE_URL", "TEST_DATABASE_URL", "ENV_FILE"):
        environment.pop(name, None)
    environment.update(
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        APP_DATA_DIR=str(tmp_path / "app-data"),
        # Model a Compose shell that inherited the development components.
        POSTGRES_USER="development",
        POSTGRES_PASSWORD="fixture",
        POSTGRES_DB="development",
    )
    if database_url is not None:
        environment["DATABASE_URL"] = database_url
    if test_database_url is not None:
        environment["TEST_DATABASE_URL"] = test_database_url
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("env_location", ["root", "working_directory"])
@pytest.mark.parametrize("dedicated_variable", [False, True])
def test_pytest_ignores_development_dotenv_and_components(
    tmp_path, env_location, dedicated_variable
):
    url = "postgresql+psycopg://test:fixture@isolated-db/test_only"
    result = run_probe(
        tmp_path,
        database_url="postgresql+psycopg://development:fixture@development-db/development"
        if dedicated_variable
        else url,
        test_database_url=url if dedicated_variable else None,
        env_location=env_location,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_pytest_requires_an_explicit_database_instead_of_discovering_development(tmp_path):
    result = run_probe(tmp_path)
    assert result.returncode != 0
    assert "Set TEST_DATABASE_URL or DATABASE_URL" in result.stdout + result.stderr


def test_runtime_uses_the_handlers_selected_env_file_without_a_second_dotenv_read(tmp_path):
    (tmp_path / ".env").write_text(
        "POSTGRES_USER=development\nPOSTGRES_PASSWORD=fixture\nPOSTGRES_DB=development\n",
        encoding="utf-8",
    )
    selected = tmp_path / "selected.env"
    selected.write_text(
        "DATABASE_URL=postgresql+psycopg://test:fixture@isolated-db/test_only\n",
        encoding="utf-8",
    )
    environment = {**os.environ, "ENV_FILE": str(selected)}
    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "DATABASE_URL"):
        environment.pop(name, None)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from sqlalchemy.engine import make_url; from src.core.config import settings; "
            "url = make_url(settings.DATABASE_URL); "
            "assert (url.host, url.database) == ('isolated-db', 'test_only')",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
