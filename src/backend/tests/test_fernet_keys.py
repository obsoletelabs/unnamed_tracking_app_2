"""Key validation and recovery never replace an installation's usable key (#249)."""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from src.core.env_handler import EnvConfigHandler
from src.core.fernet_key import persistent_fernet_key


@pytest.fixture(autouse=True)
def installation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    return tmp_path / "config"


def snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.glob("fernet.key*")}


def test_omitted_key_is_generated_once_and_survives_restart(installation):
    first = persistent_fernet_key()
    encrypted = Fernet(first.encode()).encrypt(b"saved provider credential")
    assert persistent_fernet_key() == first
    assert Fernet(first.encode()).decrypt(encrypted) == b"saved provider credential"
    assert len(snapshot(installation)) == 3


@pytest.mark.parametrize("source", ["environment", "dotenv"])
def test_invalid_explicit_key_is_rejected_without_persisting_it(installation, monkeypatch, source):
    invalid = "malformed-key-fixture"
    if source == "environment":
        monkeypatch.setenv("SECRET_KEY", invalid)
    else:
        Path(".env").write_text(f"SECRET_KEY={invalid}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="SECRET_KEY") as error:
        persistent_fernet_key()
    assert invalid not in str(error.value)
    assert "existing installation" in str(error.value)
    assert not snapshot(installation)


def test_invalid_handler_key_fails_before_any_persistence(installation):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        EnvConfigHandler({"SECRET_KEY": "invalid-fixture"}).resolved()
    assert not snapshot(installation)


def test_handler_without_a_key_does_not_read_an_unselected_dotenv(installation, monkeypatch):
    Path(".env").write_text("SECRET_KEY=unselected-invalid-fixture\n", encoding="utf-8")
    selected = installation.parent / "selected.env"
    selected.write_text("", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(selected))
    key = EnvConfigHandler().resolved()["SECRET_KEY"]
    assert Fernet(key.encode())
    assert set(snapshot(installation).values()) == {(key + "\n").encode()}


def test_invalid_environment_key_leaves_existing_copies_untouched(installation, monkeypatch):
    persistent_fernet_key()
    before = snapshot(installation)
    monkeypatch.setenv("SECRET_KEY", "invalid-fixture")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        persistent_fernet_key()
    assert snapshot(installation) == before


def test_matching_explicit_key_recovers_a_damaged_copy(installation, monkeypatch):
    key = persistent_fernet_key()
    (installation / "fernet.key.2").write_text("damaged", encoding="utf-8")
    monkeypatch.setenv("SECRET_KEY", key)
    assert persistent_fernet_key() == key
    assert set(snapshot(installation).values()) == {(key + "\n").encode()}


@pytest.mark.parametrize("keep_majority", [False, True])
def test_disagreeing_valid_copies_are_never_overwritten(installation, keep_majority):
    persistent_fernet_key()
    (installation / "fernet.key.2").write_bytes(Fernet.generate_key() + b"\n")
    if not keep_majority:
        (installation / "fernet.key.1").unlink()
    before = snapshot(installation)
    with pytest.raises(RuntimeError, match="disagree"):
        persistent_fernet_key()
    assert snapshot(installation) == before


def test_valid_environment_key_cannot_silently_replace_a_different_saved_key(
    installation, monkeypatch
):
    persistent_fernet_key()
    before = snapshot(installation)
    monkeypatch.setenv("SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="disagree"):
        persistent_fernet_key()
    assert snapshot(installation) == before


def test_unwritable_key_storage_reports_the_location_without_the_key(installation, monkeypatch):
    # A regular file in place of the directory fails even under a root test runner.
    installation.write_text("blocked", encoding="utf-8")
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SECRET_KEY", key)
    with pytest.raises(RuntimeError, match="writable persistent volume") as error:
        persistent_fernet_key()
    assert str(installation) in str(error.value)
    assert key not in str(error.value)


@pytest.mark.parametrize("damaged", [b"invalid-copy", b"\xff\xfe"])
def test_all_damaged_copies_require_recovery_instead_of_generating_a_new_key(installation, damaged):
    persistent_fernet_key()
    for path in installation.iterdir():
        path.write_bytes(damaged)
    before = snapshot(installation)
    with pytest.raises(RuntimeError, match="Restore the original matching key"):
        persistent_fernet_key()
    assert snapshot(installation) == before


def test_decryption_failure_directs_existing_installations_to_their_original_key(monkeypatch):
    from src.core import crypto

    encrypted = Fernet(Fernet.generate_key()).encrypt(b"stored credential").decode()
    monkeypatch.setattr(crypto.settings, "SECRET_KEY", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="Restore the original matching SECRET_KEY"):
            crypto.decrypt_secret(encrypted)
    finally:
        crypto._fernet.cache_clear()
