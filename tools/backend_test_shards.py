"""Split the backend pytest run into balanced shards, one set of test files per CI job.

The backend suite is database-backed and its tests create and drop schemas, so shards must never
share a database. Each shard therefore runs on its own runner with its own PostgreSQL service and
receives whole test files. Files are balanced by the durations recorded in
``tools/backend_test_shards_timings.json``; files that are not recorded yet get a default weight.

The shard count adapts to the suite: ``ceil(total estimated seconds / target seconds per shard)``,
clamped between ``MIN_SHARDS`` and a maximum. CI sets the target and maximum once, in the
``BACKEND_TEST_TARGET_SECONDS`` and ``BACKEND_TEST_MAX_SHARDS`` variables of
``.github/workflows/backend-checks.yml``; the defaults below only serve local runs.

Commands (run from anywhere; paths are relative to ``src/backend``):

* ``github-output``: print ``matrix=`` and ``count=`` lines to append to ``$GITHUB_OUTPUT``.
* ``files --shard N [--shards COUNT]``: print the test files of shard ``N`` (1-based).
* ``check``: fail unless every test file is in exactly one shard; print the estimated durations.
* ``update-timings REPORT.xml...``: rewrite the timing file from pytest JUnit XML reports.

``--target-seconds``, ``--max-shards`` and ``--shards`` (a fixed count, which skips the sizing)
apply to every command except ``update-timings``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

MIN_SHARDS = 2
DEFAULT_MAX_SHARDS = 8
# Recorded seconds of test time one shard should hold; setup adds a few minutes on top in CI.
DEFAULT_TARGET_SECONDS = 600.0
# Seconds assumed for a test file that has no recorded duration yet (cheap enough to be harmless,
# large enough that a pile of new files still spreads out instead of landing on one shard).
DEFAULT_WEIGHT = 10.0

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "src" / "backend"
TIMINGS_PATH = Path(__file__).resolve().with_name("backend_test_shards_timings.json")
TIMINGS_FORMAT = 1
# The same patterns pytest collects by default.
TEST_FILE_PATTERNS = ("test_*.py", "*_test.py")
TESTS_DIRECTORY = "tests"


class ShardError(Exception):
    """The shard plan is not a valid partition of the test files."""


def discover_test_files(backend_root: Path = BACKEND_ROOT) -> list[str]:
    """Return every collected test file as a sorted POSIX path relative to ``backend_root``."""
    found: set[str] = set()
    for pattern in TEST_FILE_PATTERNS:
        for path in (backend_root / TESTS_DIRECTORY).rglob(pattern):
            if path.is_file() and "__pycache__" not in path.parts:
                found.add(path.relative_to(backend_root).as_posix())
    return sorted(found)


def load_timings(path: Path = TIMINGS_PATH) -> dict[str, float]:
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("format") != TIMINGS_FORMAT:
        raise ShardError(f"{path} has an unsupported format: {document.get('format')!r}")
    return {name: float(seconds) for name, seconds in document["files"].items()}


def shard_count_for(
    files: Iterable[str],
    timings: Mapping[str, float],
    target_seconds: float = DEFAULT_TARGET_SECONDS,
    max_shards: int = DEFAULT_MAX_SHARDS,
    default_weight: float = DEFAULT_WEIGHT,
) -> int:
    """Number of shards that keeps each near ``target_seconds``, within the allowed range."""
    if target_seconds <= 0:
        raise ShardError("The target seconds per shard must be positive")
    if max_shards < MIN_SHARDS:
        raise ShardError(f"The maximum shard count must be at least {MIN_SHARDS}")
    total = sum(timings.get(name, default_weight) for name in files)
    return max(MIN_SHARDS, min(max_shards, math.ceil(total / target_seconds)))


def assign_shards(
    files: Iterable[str],
    timings: Mapping[str, float],
    shard_count: int,
    default_weight: float = DEFAULT_WEIGHT,
) -> list[list[str]]:
    """Greedy longest-first placement; deterministic because ties break on name and shard index."""
    if shard_count < 1:
        raise ShardError("The shard count must be at least 1")
    weights = {name: timings.get(name, default_weight) for name in files}
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    totals = [0.0] * shard_count
    for name in sorted(weights, key=lambda item: (-weights[item], item)):
        lightest = min(range(shard_count), key=lambda index: (totals[index], index))
        shards[lightest].append(name)
        totals[lightest] += weights[name]
    return [sorted(shard) for shard in shards]


def verify_assignment(shards: Sequence[Sequence[str]], files: Iterable[str]) -> None:
    """Raise unless every file appears in exactly one shard and no shard is empty."""
    expected = set(files)
    seen: dict[str, int] = {}
    duplicated: list[str] = []
    for index, shard in enumerate(shards, start=1):
        for name in shard:
            if name in seen:
                duplicated.append(f"{name} (shards {seen[name]} and {index})")
            seen[name] = index
    problems = []
    if duplicated:
        problems.append("assigned to more than one shard: " + ", ".join(sorted(duplicated)))
    unassigned = sorted(expected - set(seen))
    if unassigned:
        problems.append("not assigned to any shard: " + ", ".join(unassigned))
    unknown = sorted(set(seen) - expected)
    if unknown:
        problems.append("assigned but not a test file: " + ", ".join(unknown))
    empty = [str(index) for index, shard in enumerate(shards, start=1) if not shard]
    if empty and expected:
        problems.append("empty shards: " + ", ".join(empty))
    if problems:
        raise ShardError("Invalid backend test shard plan; " + "; ".join(problems))


def plan(
    backend_root: Path = BACKEND_ROOT,
    timings: Mapping[str, float] | None = None,
    shard_count: int | None = None,
    target_seconds: float = DEFAULT_TARGET_SECONDS,
    max_shards: int = DEFAULT_MAX_SHARDS,
) -> tuple[list[list[str]], list[str]]:
    """Discover the files, size the plan (unless a count is given), assign and verify."""
    files = discover_test_files(backend_root)
    if not files:
        raise ShardError(f"No test files found under {backend_root / TESTS_DIRECTORY}")
    recorded = load_timings() if timings is None else timings
    if shard_count is None:
        shard_count = shard_count_for(files, recorded, target_seconds, max_shards)
    shards = assign_shards(files, recorded, shard_count)
    verify_assignment(shards, files)
    return shards, files


def estimated_seconds(shard: Iterable[str], timings: Mapping[str, float]) -> float:
    return sum(timings.get(name, DEFAULT_WEIGHT) for name in shard)


def timings_from_reports(reports: Iterable[Path], files: Sequence[str]) -> dict[str, float]:
    """Sum JUnit test durations per test file.

    Pytest's default JUnit output names tests by dotted module path (``tests.sub.test_x.Class``)
    rather than by file, so the module is matched against the known test files.
    """
    known = set(files)
    modules = {name.removesuffix(".py").replace("/", "."): name for name in files}
    totals: dict[str, float] = {}
    unmatched = 0
    for report in reports:
        for case in ET.parse(report).getroot().iter("testcase"):
            name = case.get("file")
            if name is None:
                parts = (case.get("classname") or "").split(".")
                name = next(
                    (
                        modules[".".join(parts[:length])]
                        for length in range(len(parts), 0, -1)
                        if ".".join(parts[:length]) in modules
                    ),
                    None,
                )
            if name is None or name not in known:
                unmatched += 1
                continue
            totals[name] = totals.get(name, 0.0) + float(case.get("time") or 0.0)
    if not totals:
        raise ShardError("No test case in the reports matched a backend test file")
    if unmatched:
        print(f"Ignored {unmatched} test cases that matched no test file", file=sys.stderr)
    return totals


def write_timings(timings: Mapping[str, float], path: Path = TIMINGS_PATH) -> None:
    document = {
        "format": TIMINGS_FORMAT,
        "unit": "seconds",
        "files": {name: round(timings[name], 1) for name in sorted(timings)},
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _describe(shards: Sequence[Sequence[str]], timings: Mapping[str, float]) -> str:
    lines = []
    totals = [estimated_seconds(shard, timings) for shard in shards]
    for index, (shard, total) in enumerate(zip(shards, totals, strict=True), start=1):
        lines.append(f"shard {index}: {len(shard)} files, estimated {total / 60:.1f} min")
    unrecorded = sum(1 for shard in shards for name in shard if name not in timings)
    lines.append(
        f"spread {(max(totals) - min(totals)) / 60:.1f} min; "
        f"{unrecorded} files use the default weight of {DEFAULT_WEIGHT:g}s"
    )
    return "\n".join(lines)


def _env_number(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    github_output = commands.add_parser(
        "github-output", help="print the matrix and shard count for Actions"
    )
    files_command = commands.add_parser("files", help="print the test files of one shard")
    files_command.add_argument("--shard", type=int, required=True, help="1-based shard number")
    check_command = commands.add_parser(
        "check", help="verify that every test file is in exactly one shard"
    )
    for sized in (files_command, check_command, github_output):
        sized.add_argument(
            "--target-seconds",
            type=float,
            default=_env_number("BACKEND_TEST_TARGET_SECONDS", DEFAULT_TARGET_SECONDS),
        )
        sized.add_argument(
            "--max-shards",
            type=int,
            default=int(_env_number("BACKEND_TEST_MAX_SHARDS", DEFAULT_MAX_SHARDS)),
        )
        sized.add_argument("--shards", type=int, default=None, help="use this exact shard count")
    update = commands.add_parser("update-timings", help="rewrite the timing file from JUnit XML")
    update.add_argument("reports", nargs="+", type=Path)
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "update-timings":
            files = discover_test_files()
            measured = timings_from_reports(arguments.reports, files)
            # Keep recorded files the reports did not cover; drop files that no longer exist.
            merged = {name: seconds for name, seconds in load_timings().items() if name in files}
            merged.update(measured)
            write_timings(merged)
            print(f"Recorded {len(measured)} of {len(files)} test files in {TIMINGS_PATH.name}")
            return 0

        shards, files = plan(
            shard_count=arguments.shards,
            target_seconds=arguments.target_seconds,
            max_shards=arguments.max_shards,
        )
        if arguments.command == "github-output":
            print(f"matrix={json.dumps({'shard': list(range(1, len(shards) + 1))})}")
            print(f"count={len(shards)}")
        elif arguments.command == "files":
            if not 1 <= arguments.shard <= len(shards):
                raise ShardError(f"--shard must be between 1 and {len(shards)}")
            print("\n".join(shards[arguments.shard - 1]))
        else:
            print(f"{len(files)} test files, each in exactly one of {len(shards)} shards")
            print(_describe(shards, load_timings()))
    except (ShardError, OSError, ET.ParseError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
