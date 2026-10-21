"""Operator locking plus the shared, standard-library-only backup process API."""

from __future__ import annotations

import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

# Operator commands run from the checkout without installing the web application.
# The shared module imports only the standard library, never runtime configuration.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "backend"))
from src.core.backup_process import BackupError, Runner, private_directory, write_json  # noqa: E402, F401


@contextmanager
def deployment_lock(state: Path, deployment_id: str):
    import fcntl  # Linux/WSL operator CLI, never the web runtime.

    descriptor = os.open(
        state / (deployment_id + ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BackupError("unsafe-backup-lock-permissions")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("another-backup-is-running") from None
        yield
    finally:
        os.close(descriptor)
