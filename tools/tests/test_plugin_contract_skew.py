"""Exercise the plugin contract skew policy used by `Plugin repository integration`."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "plugin_contract_skew", Path(__file__).parents[1] / "plugin_contract_skew.py"
)
skew = importlib.util.module_from_spec(SPEC)
sys.modules["plugin_contract_skew"] = skew
SPEC.loader.exec_module(skew)

OLD = {
    "title": "Registration",
    "type": "object",
    "properties": {"id": {"type": "string", "maxLength": 20}, "kind": {"enum": ["a", "b"]}},
    "required": ["id"],
    "additionalProperties": False,
    "x-api-contract-version": "1.1.18",
}


def evolved(**changes):
    new = json.loads(json.dumps(OLD))
    new["x-api-contract-version"] = "1.1.19"
    for key, value in changes.items():
        new[key] = value
    return new


class VersionTests(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(skew.classify("1.1.19", "1.1.19"), "same")
        self.assertEqual(skew.classify("1.1.19", "1.1.18"), "skew")
        self.assertEqual(skew.classify("1.1.19", "1.1.20"), "ahead")
        self.assertEqual(skew.classify("1.1.19", "1.1.9"), "skew")  # numeric, not lexical
        self.assertEqual(skew.classify("1.1.19", None), "unknown")

    def test_declared_version_is_the_newest_stamp(self):
        with tempfile.TemporaryDirectory() as root:
            schemas = Path(root, "tools/schemas")
            schemas.mkdir(parents=True)
            for name, version in (("registration", "1.1.9"), ("request", "1.1.18")):
                (schemas / f"metadata-{name}-v1.schema.json").write_text(
                    json.dumps({skew.VERSION_KEY: version}), encoding="utf-8"
                )
            self.assertEqual(skew.declared_contract_version(Path(root)), "1.1.18")
            self.assertIsNone(skew.declared_contract_version(Path(root, "missing")))


class CompatibilityTests(unittest.TestCase):
    def test_identical_apart_from_version_and_prose_is_compatible(self):
        new = evolved(title="Renamed", description="More prose")
        self.assertEqual(skew.schema_problems(OLD, new), [])

    def test_new_optional_property_and_wider_enum_are_compatible(self):
        new = evolved()
        new["properties"]["extra"] = {"type": "integer"}
        new["properties"]["kind"] = {"enum": ["a", "b", "c"]}
        self.assertEqual(skew.schema_problems(OLD, new), [])

    def test_breaking_changes_are_reported(self):
        cases = {}
        removed = evolved()
        del removed["properties"]["kind"]
        cases["removed"] = removed
        required = evolved(required=["id", "kind"])
        cases["required"] = required
        narrowed = evolved()
        narrowed["properties"]["kind"] = {"enum": ["a"]}
        cases["enum"] = narrowed
        tightened = evolved()
        tightened["properties"]["id"] = {"type": "string", "maxLength": 5}
        cases["tightened"] = tightened
        retyped = evolved()
        retyped["properties"]["id"] = {"type": "integer", "maxLength": 20}
        cases["retyped"] = retyped
        constrained = evolved(minProperties=1)
        cases["constrained"] = constrained
        for name, new in cases.items():
            with self.subTest(name):
                self.assertTrue(skew.schema_problems(OLD, new))


class SnapshotTests(unittest.TestCase):
    def layout(self, root, snapshot, current, name="metadata-request-v1"):
        schemas = Path(root, "plugins/tools/schemas")
        exported = Path(root, "exported")
        schemas.mkdir(parents=True)
        exported.mkdir()
        (schemas / f"{name}.schema.json").write_text(json.dumps(snapshot), encoding="utf-8")
        (exported / f"{name}.schema.json").write_text(json.dumps(current), encoding="utf-8")
        return Path(root, "plugins"), exported

    def test_identical_snapshot_is_not_drift(self):
        with tempfile.TemporaryDirectory() as root:
            plugins, exported = self.layout(root, OLD, OLD)
            self.assertEqual(skew.compare_snapshots(plugins, exported), ({}, {}))

    def test_compatible_drift_is_listed_without_problems(self):
        with tempfile.TemporaryDirectory() as root:
            plugins, exported = self.layout(root, OLD, evolved())
            drift, incompatible = skew.compare_snapshots(plugins, exported)
            self.assertEqual(drift, {"metadata-request-v1": []})
            self.assertEqual(incompatible, {})

    def test_documents_pinned_to_an_older_version_keep_their_stamp(self):
        with tempfile.TemporaryDirectory() as root:
            plugins, exported = self.layout(root, OLD, evolved(), "x-v1")
            # Not a whole-contract document: the plugin check hard-codes its own stamp.
            (exported / "x-v1.schema.json").write_text(
                json.dumps(OLD | {"x-api-contract-version": "1.1.8"}), encoding="utf-8"
            )
            self.assertEqual(skew.compare_snapshots(plugins, exported), ({}, {}))
            stamped = json.loads((exported / f"x-v1.schema.json").read_text(encoding="utf-8"))
            self.assertEqual(stamped[skew.VERSION_KEY], "1.1.18")

    def test_incompatible_drift_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            plugins, exported = self.layout(root, OLD, evolved(required=["id", "kind"]))
            _, incompatible = skew.compare_snapshots(plugins, exported)
            self.assertIn("metadata-request-v1", incompatible)


if __name__ == "__main__":
    unittest.main()
