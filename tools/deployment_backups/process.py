"""Bounded subprocesses and private operator state. Child output never becomes an error message."""

from __future__ import annotations

import json
import os
import selectors
import signal
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


class BackupError(RuntimeError):
    def __init__(self, step: str, code: int | None = None):
        self.step = step
        self.code = code
        super().__init__(step + (f" (exit {code})" if code is not None else ""))


class Runner:
    def __init__(self, timeout: int = 1800):
        self.timeout = timeout

    def run(
        self,
        arguments: list[str],
        step: str,
        *,
        cwd: Path | None = None,
        input_file: Path | None = None,
        output_file: Path | None = None,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> bytes:
        deadline = time.monotonic() + self.timeout
        result = bytearray()
        source = None
        destination = None
        process = None
        try:
            source = input_file.open("rb") if input_file is not None else None
            if output_file is not None:
                descriptor = os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                destination = os.fdopen(descriptor, "wb")
            process = subprocess.Popen(
                arguments,
                cwd=cwd,
                stdin=source if source is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            assert process.stdout is not None
            total = 0
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BackupError(step + ":timeout")
                    for key, _events in selector.select(min(remaining, 0.2)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise BackupError(step + ":size-limit")
                        if destination is not None:
                            destination.write(chunk)
                        else:
                            result.extend(chunk)
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            if code != 0:
                raise BackupError(step, code)
            return bytes(result)
        except subprocess.TimeoutExpired:
            raise BackupError(step + ":timeout") from None
        except OSError:
            raise BackupError(step + ":io-error") from None
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                if process.stdout is not None:
                    process.stdout.close()
            if source is not None:
                source.close()
            if destination is not None:
                destination.close()

    def json(self, arguments: list[str], step: str, **options: Any) -> Any:
        try:
            return json.loads(self.run(arguments, step, **options))
        except (ValueError, UnicodeError):
            raise BackupError(step + ":invalid-json") from None


def private_directory(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise BackupError("state-directory-is-symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BackupError("state-directory-must-be-owned-and-mode-0700")
    return path


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
