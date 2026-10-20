"""Bounded, authenticated recovery evidence and its exact migration/deployment binding."""

import json
import os
import time

import pytest
from sqlalchemy.engine import make_url

from src.database.backup_gate import (
    MAX_PROOF_BYTES,
    RecoveryClaims,
    RecoveryPointError,
    database_fingerprint,
    decode_recovery_point,
    encode_recovery_point,
    migration_code_digest,
    read_recovery_point,
    validate_recovery_point,
    write_recovery_point,
)

KEY = "XJVqc8r7Uj801VzGUYhlQV8A2tZTBepjOYEiP2pTzFk="
OTHER_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
URL = make_url("postgresql+psycopg://app:private-password@db:5432/archive")


@pytest.fixture
def claims():
    return RecoveryClaims(
        version=1,
        database_fingerprint=database_fingerprint(URL, KEY),
        source_revisions=["before"],
        target_revisions=["after"],
        target_code_sha256="a" * 64,
        snapshot_id="b" * 64,
        manifest_sha256="c" * 64,
        source_image_id="sha256:" + "d" * 64,
        captured_at=1000,
        verified_at=1100,
        verification="isolated-postgresql-restore-v1",
    )


def validate(point, **changes):
    arguments = {
        "fingerprint": database_fingerprint(URL, KEY),
        "current": {"before"},
        "target": {"after"},
        "code_digest": "a" * 64,
        "now": 1200,
        "maximum_age": 7200,
    }
    return validate_recovery_point(point, **(arguments | changes))


def test_verified_point_round_trip_and_exact_checks(claims, tmp_path):
    path = tmp_path / "private" / "point.json"
    write_recovery_point(path, claims, KEY)
    restored = read_recovery_point(path, KEY)
    assert validate(restored) == claims
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert b"private-password" not in path.read_bytes()
    assert KEY.encode() not in path.read_bytes()
    assert not list(path.parent.glob("*.tmp-*"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshot_id", "e" * 64),
        ("manifest_sha256", "e" * 64),
        ("source_revisions", ["other"]),
        ("target_code_sha256", "e" * 64),
        ("captured_at", 1100),
        ("source_image_id", "sha256:" + "e" * 64),
    ],
)
def test_every_modified_signed_claim_is_rejected(claims, field, value):
    document = json.loads(encode_recovery_point(claims, KEY))
    document["claims"][field] = value
    with pytest.raises(RecoveryPointError, match="signature-invalid"):
        decode_recovery_point(json.dumps(document).encode(), KEY)


def test_another_deployment_key_cannot_verify_the_point(claims):
    with pytest.raises(RecoveryPointError, match="signature-invalid"):
        decode_recovery_point(encode_recovery_point(claims, KEY), OTHER_KEY)


@pytest.mark.parametrize(
    "changes,diagnostic",
    [
        ({"fingerprint": "f" * 64}, "database-mismatch"),
        ({"current": {"different"}}, "revision-mismatch"),
        ({"target": {"different"}}, "revision-mismatch"),
        ({"code_digest": "f" * 64}, "target-code-mismatch"),
        ({"now": 8301}, "expired"),
        ({"now": 700}, "timestamps-invalid"),
        ({"maximum_age": 0}, "age-policy-invalid"),
    ],
)
def test_reuse_is_limited_to_the_same_recent_source_and_target(claims, changes, diagnostic):
    with pytest.raises(RecoveryPointError, match=diagnostic):
        validate(claims, **changes)


def test_capture_time_not_late_verification_time_controls_freshness(claims):
    point = claims.model_copy(update={"verified_at": 8200})
    with pytest.raises(RecoveryPointError, match="expired"):
        validate(point, now=8301)


def test_credential_rotation_does_not_change_database_identity_but_destinations_do():
    original = database_fingerprint(URL, KEY)
    assert database_fingerprint(URL.set(password="rotated"), KEY) == original
    for changed in (
        URL.set(host="other"),
        URL.set(database="other"),
        URL.set(username="other"),
        URL.set(query={"host": "other"}),
    ):
        assert database_fingerprint(changed, KEY) != original
    assert database_fingerprint(URL, OTHER_KEY) != original


def test_actual_target_source_bytes_and_paths_control_approval(tmp_path):
    (tmp_path / "versions").mkdir()
    source = tmp_path / "versions" / "upgrade.py"
    source.write_text("# original\n")
    before = migration_code_digest(tmp_path)
    assert migration_code_digest(tmp_path) == before
    source.write_text("# changed migration\n")
    assert migration_code_digest(tmp_path) != before
    source.write_text("# original\n")
    source.rename(source.with_name("renamed.py"))
    assert migration_code_digest(tmp_path) != before


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{",
        b"\xff",
        b"x" * (MAX_PROOF_BYTES + 1),
        b'{"claims":{},"claims":{},"signature":"private-password"}',
    ],
)
def test_malformed_oversized_or_ambiguous_documents_have_constant_diagnostics(data):
    with pytest.raises(RecoveryPointError) as raised:
        decode_recovery_point(data, KEY)
    assert "private-password" not in str(raised.value)


def test_missing_unsafe_and_nonregular_proof_files_fail_without_blocking(claims, tmp_path):
    path = tmp_path / "point.json"
    with pytest.raises(RecoveryPointError, match="required"):
        read_recovery_point(path, KEY)
    path.write_bytes(encode_recovery_point(claims, KEY))
    path.chmod(0o644)
    with pytest.raises(RecoveryPointError, match="permissions-invalid"):
        read_recovery_point(path, KEY)
    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(RecoveryPointError, match="unreadable"):
        read_recovery_point(link, KEY)
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo, 0o600)
    started = time.monotonic()
    with pytest.raises(RecoveryPointError, match="permissions-invalid"):
        read_recovery_point(fifo, KEY)
    assert time.monotonic() - started < 1


def test_private_directory_failure_does_not_replace_an_existing_point(claims, tmp_path):
    point = tmp_path / "private" / "point.json"
    write_recovery_point(point, claims, KEY)
    original = point.read_bytes()
    point.parent.chmod(0o755)
    with pytest.raises(RecoveryPointError, match="directory-permissions-invalid"):
        write_recovery_point(point, claims, KEY)
    assert point.read_bytes() == original


@pytest.mark.parametrize("version", [None, True, 1.0, "1"])
def test_version_cannot_be_omitted_or_coerced(claims, version):
    document = json.loads(encode_recovery_point(claims, KEY))
    if version is None:
        document["claims"].pop("version")
    else:
        document["claims"]["version"] = version
    with pytest.raises(RecoveryPointError, match="document-invalid"):
        decode_recovery_point(json.dumps(document).encode(), KEY)
