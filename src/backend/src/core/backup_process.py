"""Shared bounded backup subprocesses and private files; no application imports."""

from __future__ import annotations

import json
import os
import selectors
import signal
import stat
import subprocess
import time
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any, BinaryIO
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
        timeout: float | None = None,
        environment: Mapping[str, str] | None = None,
        inherit_environment: bool = True,
    ) -> bytes:
        deadline = time.monotonic() + (
            self.timeout if timeout is None else min(self.timeout, timeout)
        )
        try:
            with ExitStack() as resources:
                source = resources.enter_context(input_file.open("rb")) if input_file else None
                destination = None
                if output_file is not None:
                    destination = resources.enter_context(
                        os.fdopen(
                            os.open(output_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                            "wb",
                        )
                    )
                with subprocess.Popen(
                    arguments,
                    cwd=cwd,
                    stdin=source if source is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=(
                        None
                        if environment is None and inherit_environment
                        else {**(os.environ if inherit_environment else {}), **(environment or {})}
                    ),
                ) as process:
                    try:
                        return self._read_output(process, step, deadline, max_bytes, destination)
                    finally:
                        if process.poll() is None:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait()
        except subprocess.TimeoutExpired:
            raise BackupError(step + ":timeout") from None
        except OSError:
            raise BackupError(step + ":io-error") from None

    @staticmethod
    def _read_output(
        process: subprocess.Popen[bytes],
        step: str,
        deadline: float,
        max_bytes: int,
        destination: BinaryIO | None,
    ) -> bytes:
        """Drain output without an unbounded allocation or a blocked pipe."""
        assert process.stdout is not None
        result = bytearray()
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
