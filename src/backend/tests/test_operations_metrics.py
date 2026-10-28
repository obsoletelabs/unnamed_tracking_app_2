"""Metrics are emitted only for observed backup and persisted migration outcomes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes import password_recovery, user_invitations
from src.core.metrics import REGISTRY
from src.database import migration_safety
from src.features.backup import scheduler


def test_offline_recovery_invalidation_does_not_require_database_settings(tmp_path: Path) -> None:
    """The one-shot recovery tool must import before application settings exist."""
    backend = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "src.database.prepare_upgrade", "--invalidate"],
        cwd=backend,
        env={"PYTHONPATH": str(backend), "APP_DATA_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_library_backup_verification_counts_only_successful_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def snapshot(*_args):
        return SimpleNamespace(
            games=[1],
            movies=[],
            tv_shows=[],
            anime=[],
            model_dump=lambda **_kwargs: {"games": [1], "exported_at": 123},
        )

    monkeypatch.setattr(scheduler, "SessionLocal", _Session)
    monkeypatch.setattr(scheduler, "build_library_export", snapshot)
    monkeypatch.setattr(scheduler, "_BACKUP_ROOT", tmp_path)
    labels = {"backend": "library_json", "operation": "verify", "outcome": "succeeded"}
    before = REGISTRY.get_sample_value("uta_backup_operations_total", labels) or 0
    path = await scheduler.run_backup_for_user(uuid4())
    assert path is not None and json.loads(path.read_text()) == {"games": [1], "exported_at": 123}
    assert REGISTRY.get_sample_value("uta_backup_operations_total", labels) == before + 1
    assert (
        REGISTRY.get_sample_value("uta_backup_archive_bytes", {"backend": "library_json"})
        == path.stat().st_size
    )

    original_read = Path.read_bytes

    def bad_read(self):
        if self.parent.parent == tmp_path:
            return b"damaged"
        return original_read(self)

    monkeypatch.setattr(Path, "read_bytes", bad_read)
    failed = {"backend": "library_json", "operation": "verify", "outcome": "failed"}
    failed_before = REGISTRY.get_sample_value("uta_backup_operations_total", failed) or 0
    with pytest.raises(OSError, match="did not match"):
        await scheduler.run_backup_for_user(uuid4())
    assert list(tmp_path.rglob("backup-*.json")) == [path]
    assert REGISTRY.get_sample_value("uta_backup_operations_total", failed) == failed_before + 1
    assert REGISTRY.get_sample_value("uta_backup_operations_total", labels) == before + 1


def test_migration_report_publishes_only_fixed_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(migration_safety, "recovery_directory", lambda: tmp_path)
    (tmp_path / "attempt-01.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "verified_at": 1234,
                "backup_id": "secret",
                "started_at": 100,
                "finished_at": 120,
            }
        )
    )
    migration_safety.publish_last_recovery_status()
    assert REGISTRY.get_sample_value("uta_migration_recovery_status", {"status": "succeeded"}) == 1
    assert (
        REGISTRY.get_sample_value(
            "uta_backup_last_verified_timestamp_seconds", {"backend": "migration_recovery"}
        )
        == 1234
    )
    assert REGISTRY.get_sample_value("uta_migration_maintenance_duration_seconds") == 20


@pytest.mark.asyncio
async def test_recovery_and_invitation_terminal_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def invalid(*_args):
        return False

    monkeypatch.setattr(password_recovery, "complete_password_reset", invalid)
    monkeypatch.setattr(user_invitations, "accept_invitation", invalid)
    reset = password_recovery.ResetCompletion(id=uuid4(), token="t" * 32, password="secret")
    invite = user_invitations.InviteAcceptance(
        id=uuid4(), token="t" * 32, username="reader", password="secret"
    )
    recovery_labels = {"flow": "recovery_complete", "outcome": "invalid"}
    invite_labels = {"flow": "invitation_accept", "outcome": "invalid"}
    recovery_before = REGISTRY.get_sample_value("uta_auth_attempts_total", recovery_labels) or 0
    invite_before = REGISTRY.get_sample_value("uta_auth_attempts_total", invite_labels) or 0
    with pytest.raises(HTTPException):
        await password_recovery.complete_reset(reset, _Session())
    with pytest.raises(HTTPException):
        await user_invitations.accept(invite, _Session())
    assert (
        REGISTRY.get_sample_value("uta_auth_attempts_total", recovery_labels) == recovery_before + 1
    )
    assert REGISTRY.get_sample_value("uta_auth_attempts_total", invite_labels) == invite_before + 1
