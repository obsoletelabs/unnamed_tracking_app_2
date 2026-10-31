"""Exercise backend test sharding: balance, determinism, coverage checks and timing refresh."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "backend_test_shards", Path(__file__).parents[1] / "backend_test_shards.py"
)
shards_tool = importlib.util.module_from_spec(SPEC)
sys.modules["backend_test_shards"] = shards_tool
SPEC.loader.exec_module(shards_tool)

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
<testcase classname="tests.test_a" name="test_one" time="1.50"/>
<testcase classname="tests.test_a.TestGroup" name="test_two" time="2.25"/>
<testcase classname="tests.sub.test_b" name="test_three" time="4.00"/>
<testcase classname="tests.test_unknown" name="test_four" time="9.00"/>
</testsuite></testsuites>
"""


class AssignmentTests(unittest.TestCase):
    def test_every_file_lands_in_exactly_one_shard(self):
        files = [f"tests/test_{index}.py" for index in range(23)]
        timings = {name: float(index + 1) for index, name in enumerate(files)}
        shards = shards_tool.assign_shards(files, timings, 4)
        shards_tool.verify_assignment(shards, files)
        self.assertEqual(sorted(name for shard in shards for name in shard), sorted(files))

    def test_assignment_is_deterministic_and_independent_of_input_order(self):
        files = [f"tests/test_{index}.py" for index in range(17)]
        timings = {name: float(index % 5 + 1) for index, name in enumerate(files)}
        first = shards_tool.assign_shards(files, timings, 3)
        self.assertEqual(first, shards_tool.assign_shards(list(reversed(files)), timings, 3))
        self.assertEqual(first, shards_tool.assign_shards(files, timings, 3))

    def test_shards_are_balanced_by_recorded_duration(self):
        files = [
            "tests/test_big.py",
            "tests/test_mid.py",
            "tests/test_a.py",
            "tests/test_b.py",
        ]
        timings = {
            "tests/test_big.py": 100,
            "tests/test_mid.py": 50,
            "tests/test_a.py": 50,
            "tests/test_b.py": 50,
        }
        shards = shards_tool.assign_shards(files, timings, 2)
        totals = sorted(shards_tool.estimated_seconds(shard, timings) for shard in shards)
        self.assertEqual(totals, [100, 150])

    def test_unrecorded_files_use_the_default_weight(self):
        files = [f"tests/test_{index}.py" for index in range(8)]
        shards = shards_tool.assign_shards(files, {}, 4)
        self.assertEqual([len(shard) for shard in shards], [2, 2, 2, 2])
        timings = {"tests/test_0.py": shards_tool.DEFAULT_WEIGHT * 100}
        shards = shards_tool.assign_shards(files, timings, 4)
        self.assertEqual(
            [shard for shard in shards if "tests/test_0.py" in shard],
            [["tests/test_0.py"]],
        )

    def test_verification_rejects_missing_duplicate_and_unknown_files(self):
        files = ["tests/test_a.py", "tests/test_b.py"]
        with self.assertRaisesRegex(
            shards_tool.ShardError, "not assigned to any shard: tests/test_b"
        ):
            shards_tool.verify_assignment([["tests/test_a.py"], []], files)
        with self.assertRaisesRegex(shards_tool.ShardError, "more than one shard: tests/test_a.py"):
            shards_tool.verify_assignment([files, ["tests/test_a.py"]], files)
        with self.assertRaisesRegex(shards_tool.ShardError, "not a test file: tests/test_c.py"):
            shards_tool.verify_assignment([files, ["tests/test_c.py"]], files)
        with self.assertRaisesRegex(shards_tool.ShardError, "empty shards"):
            shards_tool.verify_assignment([files, []], files)

    def test_invalid_shard_count_is_rejected(self):
        with self.assertRaises(shards_tool.ShardError):
            shards_tool.assign_shards(["tests/test_a.py"], {}, 0)


class SizingTests(unittest.TestCase):
    files = [f"tests/test_{index}.py" for index in range(10)]

    def test_count_follows_total_time_and_is_clamped(self):
        timings = {name: 100.0 for name in self.files}  # 1000s in total
        self.assertEqual(shards_tool.shard_count_for(self.files, timings, 300, 8), 4)
        self.assertEqual(shards_tool.shard_count_for(self.files, timings, 10_000, 8), 2)
        self.assertEqual(shards_tool.shard_count_for(self.files, timings, 10, 8), 8)

    def test_new_files_count_at_the_default_weight(self):
        weight = shards_tool.DEFAULT_WEIGHT
        self.assertEqual(shards_tool.shard_count_for(self.files, {}, weight * 2, 8), 5)

    def test_invalid_limits_are_rejected(self):
        with self.assertRaises(shards_tool.ShardError):
            shards_tool.shard_count_for(self.files, {}, 0, 8)
        with self.assertRaises(shards_tool.ShardError):
            shards_tool.shard_count_for(self.files, {}, 100, 1)


class RepositoryPlanTests(unittest.TestCase):
    def test_the_real_suite_is_fully_and_uniquely_assigned(self):
        shards, files = shards_tool.plan()
        shards_tool.verify_assignment(shards, files)
        self.assertGreaterEqual(len(shards), shards_tool.MIN_SHARDS)
        self.assertLessEqual(len(shards), shards_tool.DEFAULT_MAX_SHARDS)

    def test_github_output_lists_matrix_and_count(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(shards_tool.main(["github-output"]), 0)
        lines = dict(line.split("=", 1) for line in buffer.getvalue().splitlines())
        count = int(lines["count"])
        self.assertEqual(json.loads(lines["matrix"]), {"shard": list(range(1, count + 1))})

    def test_a_fixed_shard_count_reproduces_the_planned_assignment(self):
        planned, _ = shards_tool.plan()
        fixed, _ = shards_tool.plan(shard_count=len(planned))
        self.assertEqual(planned, fixed)

    def test_the_workflow_sizing_variables_are_present(self):
        workflow = (Path(__file__).parents[2] / ".github/workflows/backend-checks.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("BACKEND_TEST_TARGET_SECONDS:", workflow)
        self.assertIn("BACKEND_TEST_MAX_SHARDS:", workflow)

    def test_every_recorded_timing_belongs_to_an_existing_test_file(self):
        files = set(shards_tool.discover_test_files())
        self.assertFalse(
            set(shards_tool.load_timings()) - files, "stale entries in the timing file"
        )


class DiscoveryAndTimingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def touch(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def test_discovery_finds_nested_test_files_only(self):
        for name in (
            "tests/test_a.py",
            "tests/sub/test_b.py",
            "tests/sub/helper_test.py",
            "tests/conftest.py",
            "tests/helpers.py",
            "tests/__pycache__/test_c.py",
        ):
            self.touch(name)
        self.assertEqual(
            shards_tool.discover_test_files(self.root),
            ["tests/sub/helper_test.py", "tests/sub/test_b.py", "tests/test_a.py"],
        )

    def test_timings_are_summed_per_file_from_junit_reports(self):
        report = self.root / "report.xml"
        report.write_text(JUNIT, encoding="utf-8")
        totals = shards_tool.timings_from_reports(
            [report], ["tests/test_a.py", "tests/sub/test_b.py"]
        )
        self.assertEqual(totals, {"tests/test_a.py": 3.75, "tests/sub/test_b.py": 4.0})

    def test_a_report_matching_no_file_is_an_error(self):
        report = self.root / "report.xml"
        report.write_text(JUNIT, encoding="utf-8")
        with self.assertRaises(shards_tool.ShardError):
            shards_tool.timings_from_reports([report], ["tests/test_other.py"])

    def test_timing_file_round_trips_and_rejects_unknown_formats(self):
        path = self.root / "timings.json"
        shards_tool.write_timings({"tests/test_b.py": 2.04, "tests/test_a.py": 1.0}, path)
        self.assertEqual(
            shards_tool.load_timings(path),
            {"tests/test_a.py": 1.0, "tests/test_b.py": 2.0},
        )
        path.write_text(json.dumps({"format": 99, "files": {}}), encoding="utf-8")
        with self.assertRaises(shards_tool.ShardError):
            shards_tool.load_timings(path)


if __name__ == "__main__":
    unittest.main()
