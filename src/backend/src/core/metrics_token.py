"""A persistent scrape token generated for the operator when none is configured."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

TOKEN_BYTES = 32  # 64 hexadecimal characters


def token_path() -> Path:
    return Path(os.getenv("APP_DATA_DIR", "/data")).expanduser() / "config" / "metrics.token"


def persistent_metrics_token() -> str:
    """Read the saved token, creating it (mode 0600) on first use.

    The file lives beside the other deployment secrets so it survives restarts and is included
    in the same volume backups. Processes starting together cannot overwrite each other: the
    file is created exclusively and the loser reads the winner's value."""
    path = token_path()
    for _ in range(3):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            value = ""
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(
                f"Could not read the metrics token in {path.parent}. Mount APP_DATA_DIR as a "
                "writable persistent volume, or set METRICS_TOKEN."
            ) from exc
        if len(value) >= 32:
            return value
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if value:  # a damaged or short file is never overwritten silently
                raise RuntimeError(
                    f"The metrics token file {path} is shorter than 32 characters. Delete it "
                    "to generate a new one, or set METRICS_TOKEN."
                ) from None
            continue
        except OSError as exc:
            raise RuntimeError(
                f"Could not create a metrics token in {path.parent}. Mount APP_DATA_DIR as a "
                "writable persistent volume, or set METRICS_TOKEN."
            ) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_hex(TOKEN_BYTES))
    raise RuntimeError(f"Could not establish the metrics token in {path.parent}.")
