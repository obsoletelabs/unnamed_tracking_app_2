"""Plugin API 1.1.14 notification documents: schemas, conformance and exporter agreement."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.plugin_api import (
    PLUGIN_NOTIFICATION_DOCUMENTS,
    plugin_notification_schemas,
)
from src.plugin_api.base_contracts import PLUGIN_API_CONTRACT_VERSION
from src.plugin_api.notification_story_contracts import NOTIFICATION_CONTRACT_VERSION

FIXTURES = Path(__file__).parent / "fixtures" / "notification_contract"
CONFORMANCE = json.loads((FIXTURES / "plugin_conformance.json").read_text(encoding="utf-8"))
ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "item", CONFORMANCE["cases"], ids=[item["name"] for item in CONFORMANCE["cases"]]
)
def test_plugin_conformance_cases(item):
    """The same JSON fixtures are the companion SDK's validation contract."""
    model = PLUGIN_NOTIFICATION_DOCUMENTS[item["document_type"]]
    if item["valid"]:
        parsed = model.model_validate(item["document"])
        assert model.model_validate_json(parsed.model_dump_json()) == parsed
        return
    with pytest.raises(ValidationError) as failure:
        model.model_validate(item["document"])
    assert len(failure.value.errors()) == 1


def test_conformance_covers_every_document_in_both_directions():
    assert CONFORMANCE["api_contract_version"] == PLUGIN_API_CONTRACT_VERSION
    assert CONFORMANCE["contract_version"] == NOTIFICATION_CONTRACT_VERSION
    for name in PLUGIN_NOTIFICATION_DOCUMENTS:
        outcomes = {item["valid"] for item in CONFORMANCE["cases"] if item["document_type"] == name}
        assert outcomes == {True, False}, name


@pytest.mark.parametrize("name", sorted(PLUGIN_NOTIFICATION_DOCUMENTS))
def test_published_schemas_match_reviewed_snapshots(name):
    schema = plugin_notification_schemas()[name]
    snapshot = json.loads((FIXTURES / "plugin_schemas" / f"{name}.schema.json").read_text("utf-8"))
    assert schema == snapshot
    assert schema["x-api-contract-version"] == PLUGIN_API_CONTRACT_VERSION
    assert schema["additionalProperties"] is False


def test_snapshot_directory_has_no_stale_schemas():
    published = {path.name for path in (FIXTURES / "plugin_schemas").glob("*.schema.json")}
    assert published == {f"{name}.schema.json" for name in PLUGIN_NOTIFICATION_DOCUMENTS}


def test_exporter_publishes_every_document_the_sdk_pins(tmp_path):
    """One command produces the legacy, canonical and 1.1.14 schemas for the companion SDK."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "export_notification_contract", ROOT / "tools" / "export_notification_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.export(tmp_path)
    names = {path.name.removesuffix(".schema.json") for path in tmp_path.glob("*.schema.json")}
    assert names >= set(PLUGIN_NOTIFICATION_DOCUMENTS)
    assert {
        "notification-type-v1",
        "notification-provider-v1",
        "notification-canonical-event-v1",
    } <= names
    provider = json.loads((tmp_path / "notification-provider-v1.schema.json").read_text("utf-8"))
    assert provider["x-api-contract-version"] == PLUGIN_API_CONTRACT_VERSION
    for name in PLUGIN_NOTIFICATION_DOCUMENTS:
        exported = json.loads((tmp_path / f"{name}.schema.json").read_text("utf-8"))
        snapshot = json.loads(
            (FIXTURES / "plugin_schemas" / f"{name}.schema.json").read_text("utf-8")
        )
        assert exported == snapshot


def test_older_documents_keep_their_pinned_versions(tmp_path):
    """Only the provider and lifecycle schemas gained (optional) 1.1.14 fields; the rest keep
    their introduction versions, so older SDKs keep validating against them."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "export_notification_contract", ROOT / "tools" / "export_notification_contract.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.export(tmp_path)
    expected = {
        "notification-type-v1": "1.1.2",
        "notification-event-v1": "1.1.2",
        "notification-layout-v1": "1.1.2",
        "notification-content-v1": "1.1.5",
        "notification-destination-v1": "1.1.5",
    }
    for name in ("notification-lifecycle-query-v1", "notification-lifecycle-page-v1"):
        schema = json.loads((tmp_path / f"{name}.schema.json").read_text("utf-8"))
        assert schema["x-api-contract-version"] == PLUGIN_API_CONTRACT_VERSION
    for name, version in expected.items():
        schema = json.loads((tmp_path / f"{name}.schema.json").read_text("utf-8"))
        assert schema["x-api-contract-version"] == version
