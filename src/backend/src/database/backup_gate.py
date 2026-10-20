"""Private, deployment-bound recovery attestations for the migration runner.

Issuing is reserved for the operator workflow after restoring and checking its
Restic recovery point. The runner can verify a bounded local document;
repository credentials and Docker access never enter the web application.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.engine import URL

MAX_PROOF_BYTES = 16384
_HEX = r"^[0-9a-f]{64}$"


class RecoveryPointError(RuntimeError):
    """Constant operator diagnostics; never include document contents or credentials."""


class RecoveryClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1]
    database_fingerprint: str = Field(pattern=_HEX)
    source_revisions: list[str] = Field(max_length=32)
    target_revisions: list[str] = Field(min_length=1, max_length=1)
    target_code_sha256: str = Field(pattern=_HEX)
    snapshot_id: str = Field(pattern=_HEX)
    manifest_sha256: str = Field(pattern=_HEX)
    source_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    captured_at: int = Field(ge=0, le=2**63 - 1)
    verified_at: int = Field(ge=0, le=2**63 - 1)
    verification: Literal["isolated-postgresql-restore-v1"]

    @field_validator("version", mode="before")
    @classmethod
    def version_is_an_integer(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Invalid recovery document version")
        return value

    @field_validator("source_revisions", "target_revisions")
    @classmethod
    def revisions_are_canonical(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)) or any(
            not revision
            or len(revision) > 128
            or not revision.isascii()
            or any(ord(character) < 32 or ord(character) > 126 for character in revision)
            for revision in value
        ):
            raise ValueError("Invalid recovery revision set")
        return value


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    claims: RecoveryClaims
    signature: str = Field(pattern=_HEX)


def _key(secret: str, purpose: bytes) -> bytes:
    try:
        material = base64.b64decode(secret.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError):
        raise RecoveryPointError("recovery-point-key-invalid") from None
    if len(material) != 32:
        raise RecoveryPointError("recovery-point-key-invalid")
    return hmac.digest(material, b"uta:migration-recovery:v1:" + purpose, "sha256")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def database_fingerprint(url: URL, secret: str) -> str:
    """Bind the point to this deployment key and configured database, never its password."""
    identity = {
        "host": (url.host or "unix-socket").lower().rstrip("."),
        "port": url.port or 5432,
        "database": url.database or "",
        "username": url.username or "",
        "connection_options": {
            name: value
            for name, value in url.query.items()
            if name in {"host", "hostaddr", "port", "dbname", "user", "service", "options"}
        },
    }
    return hmac.new(_key(secret, b"database"), _canonical(identity), hashlib.sha256).hexdigest()


def migration_code_digest(database_source: Path) -> str:
    """Bind a point to the actual target database models, runner and migration sources."""
    paths = sorted(database_source.rglob("*.py"))
    if not paths or len(paths) > 10000:
        raise RecoveryPointError("recovery-target-code-invalid")
    result, total = hashlib.sha256(), 0
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RecoveryPointError("recovery-target-code-invalid")
        total += path.stat().st_size
        if total > 64 * 1024**2:
            raise RecoveryPointError("recovery-target-code-too-large")
        relative = path.relative_to(database_source).as_posix().encode()
        content = path.read_bytes()
        result.update(len(relative).to_bytes(8, "big") + relative)
        result.update(len(content).to_bytes(8, "big") + content)
    return result.hexdigest()


def encode_recovery_point(claims: RecoveryClaims, secret: str) -> bytes:
    payload = claims.model_dump(mode="json")
    signature = hmac.new(
        _key(secret, b"signature"), _canonical(payload), hashlib.sha256
    ).hexdigest()
    return _canonical({"claims": payload, "signature": signature}) + b"\n"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate recovery document key")
        result[key] = value
    return result


def decode_recovery_point(data: bytes, secret: str) -> RecoveryClaims:
    if not data or len(data) > MAX_PROOF_BYTES:
        raise RecoveryPointError("recovery-point-size-invalid")
    try:
        envelope = _Envelope.model_validate(json.loads(data, object_pairs_hook=_unique_object))
    except (ValidationError, ValueError, UnicodeError):
        raise RecoveryPointError("recovery-point-document-invalid") from None
    expected = hmac.new(
        _key(secret, b"signature"),
        _canonical(envelope.claims.model_dump(mode="json")),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(envelope.signature, expected):
        raise RecoveryPointError("recovery-point-signature-invalid")
    return envelope.claims


def read_recovery_point(path: Path, secret: str) -> RecoveryClaims:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RecoveryPointError("recovery-point-permissions-invalid")
            return decode_recovery_point(stream.read(MAX_PROOF_BYTES + 1), secret)
    except FileNotFoundError:
        raise RecoveryPointError("verified-pre-upgrade-recovery-point-required") from None
    except OSError:
        raise RecoveryPointError("recovery-point-unreadable") from None


def write_recovery_point(path: Path, claims: RecoveryClaims, secret: str) -> None:
    """Atomically replace only the operator-selected private attestation file."""
    write_private_document(path, encode_recovery_point(claims, secret))


def write_private_document(path: Path, data: bytes) -> None:
    """Durably replace a private local recovery document, refusing unsafe directories."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.stat()
    if path.parent.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RecoveryPointError("recovery-point-directory-permissions-invalid")
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def validate_recovery_point(
    claims: RecoveryClaims,
    *,
    fingerprint: str,
    current: set[str],
    target: set[str],
    code_digest: str,
    now: int,
    maximum_age: int,
) -> RecoveryClaims:
    if maximum_age < 60 or maximum_age > 7 * 86400:
        raise RecoveryPointError("recovery-point-age-policy-invalid")
    if not hmac.compare_digest(claims.database_fingerprint, fingerprint):
        raise RecoveryPointError("recovery-point-database-mismatch")
    if set(claims.source_revisions) != current or set(claims.target_revisions) != target:
        raise RecoveryPointError("recovery-point-revision-mismatch")
    if not hmac.compare_digest(claims.target_code_sha256, code_digest):
        raise RecoveryPointError("recovery-point-target-code-mismatch")
    if not claims.captured_at <= claims.verified_at <= now + 300:
        raise RecoveryPointError("recovery-point-timestamps-invalid")
    if claims.captured_at > now + 300 or now - claims.captured_at > maximum_age:
        raise RecoveryPointError("recovery-point-expired")
    return claims
