"""Versioned, checksummed recovery bundles. Archives are inspected, never extracted here."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from deployment_backups.process import BackupError, write_json


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_archive(path: Path, root: str) -> None:
    """Reject truncation, ambiguous names, special files and links escaping the mount."""
    if path.stat().st_size < 1024 or path.stat().st_size % 512:
        raise BackupError("invalid-persistent-archive-size")
    with path.open("rb") as stream:
        stream.seek(-1024, 2)
        if stream.read() != bytes(1024):
            raise BackupError("persistent-archive-missing-end-marker")
    names: dict[str, tarfile.TarInfo] = {}
    links = {}
    try:
        with tarfile.open(path, "r|") as archive:
            for member in archive:
                if len(names) >= 100000:
                    raise BackupError("persistent-archive-entry-limit")
                name = str(PurePosixPath(member.name))
                if (
                    name in names
                    or member.name.startswith("/")
                    or ".." in PurePosixPath(member.name).parts
                    or not (name == root or name.startswith(root + "/"))
                    or not (member.isfile() or member.isdir() or member.issym() or member.islnk())
                ):
                    raise BackupError("unsafe-persistent-archive-entry")
                names[name] = member
                if member.issym() or member.islnk():
                    if member.linkname.startswith("/"):
                        raise BackupError("unsafe-persistent-archive-link")
                    target = posixpath.normpath(
                        posixpath.join(posixpath.dirname(name), member.linkname)
                        if member.issym()
                        else member.linkname
                    )
                    if not (target == root or target.startswith(root + "/")):
                        raise BackupError("unsafe-persistent-archive-link")
                    links[name] = target
        if root not in names:
            raise BackupError("persistent-archive-missing-root")
        # Do not restore a file through a symbolic-link parent, even if a particular tar
        # implementation would follow it. Link chains must terminate within this archive.
        for name in names:
            if any(str(parent) in links for parent in PurePosixPath(name).parents):
                raise BackupError("persistent-archive-entry-below-link")
        for name, target in links.items():
            seen = {name}
            while target in links:
                if target in seen:
                    raise BackupError("persistent-archive-link-cycle")
                seen.add(target)
                target = links[target]
            if target not in names:
                raise BackupError("persistent-archive-link-target-missing")
            if names[name].islnk() and not names[target].isfile():
                raise BackupError("persistent-archive-invalid-hard-link")
    except (tarfile.TarError, EOFError, OSError):
        raise BackupError("invalid-persistent-archive") from None


def load_json(path: Path, maximum: int = 4 * 1024 * 1024) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise BackupError("invalid-bundle-metadata-file")
    try:
        return json.loads(path.read_bytes())
    except (ValueError, UnicodeError):
        raise BackupError("invalid-bundle-metadata-json") from None


def validate_contents(directory: Path, max_bytes: int) -> dict[str, Any]:
    metadata = load_json(directory / "deployment.json")
    if set(metadata["services"]) != {"app", "runtime", "database"}:
        raise BackupError("bundle-missing-required-service")
    mounts = metadata["mounts"]
    if not mounts or not all(isinstance(mount, dict) for mount in mounts):
        raise BackupError("bundle-missing-persistent-mounts")
    if not {"app", "runtime"}.issubset({mount["role"] for mount in mounts}):
        raise BackupError("bundle-missing-required-mount")
    required = {"database.dump", "deployment.json"}
    for index, mount in enumerate(mounts):
        if mount["archive"] != f"mount-{index:04}.tar":
            raise BackupError("invalid-bundle-mount-archive-name")
        destination = PurePosixPath(mount["destination"])
        if (
            not destination.is_absolute()
            or ".." in destination.parts
            or destination == PurePosixPath("/")
        ):
            raise BackupError("invalid-bundle-mount-destination")
        required.add(mount["archive"])
    entries = list(directory.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in entries):
        raise BackupError("bundle-must-contain-only-regular-files")
    names = {path.name for path in entries}
    configurations = {name for name in names if re.fullmatch(r"compose-[0-9]{4}\.yaml", name)}
    if not configurations or names - {"manifest.json"} != required | configurations:
        raise BackupError("bundle-incomplete-or-unexpected-files")
    if sum(path.stat().st_size for path in entries) > max_bytes:
        raise BackupError("bundle-size-limit")
    with (directory / "database.dump").open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise BackupError("bundle-missing-postgresql-custom-dump")
    for mount in mounts:
        inspect_archive(directory / mount["archive"], PurePosixPath(mount["destination"]).name)
    return metadata


def seal(directory: Path, deployment_id: str, run_id: str, max_bytes: int) -> dict[str, Any]:
    validate_contents(directory, max_bytes)
    manifest = {
        "format": "unnamed-tracking-deployment-backup",
        "version": 1,
        "deployment_id": deployment_id,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "files": {
            path.name: {"sha256": digest(path), "bytes": path.stat().st_size}
            for path in sorted(directory.iterdir())
        },
    }
    write_json(directory / "manifest.json", manifest)
    verify(directory, deployment_id, max_bytes)
    return manifest


def verify(directory: Path, deployment_id: str, max_bytes: int) -> dict[str, Any]:
    manifest = load_json(directory / "manifest.json")
    if (
        manifest.get("format") != "unnamed-tracking-deployment-backup"
        or manifest.get("version") != 1
        or manifest.get("deployment_id") != deployment_id
    ):
        raise BackupError("bundle-version-or-deployment-mismatch")
    metadata = validate_contents(directory, max_bytes)
    if set(manifest["files"]) != {path.name for path in directory.iterdir()} - {"manifest.json"}:
        raise BackupError("bundle-manifest-file-set-mismatch")
    for name, expected in manifest["files"].items():
        path = directory / name
        if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
            raise BackupError("bundle-file-integrity-failed")
    return metadata
