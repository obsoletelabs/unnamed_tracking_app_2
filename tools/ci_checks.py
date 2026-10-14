"""Run full quality checks for ready PRs/main, and scoped checks for branch pushes.

Tests deliberately do not use this selector. Filenames stay in subprocess
argument lists rather than being interpolated into shell commands.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = "src/backend/"
FRONTEND = "src/frontend/"
FRONTEND_SOURCE = (".vue", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css")
FRONTEND_FORMAT = (*FRONTEND_SOURCE, ".json", ".html", ".md", ".yaml", ".yml", ".scss")
SHARED_CONFIG = {"tools/ci_checks.py", ".github/actions/changed-files/action.yml"}


def git(*arguments: str, root: Path = ROOT) -> list[str]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    if "-z" in arguments:
        return result.stdout.decode("utf-8").split("\0")[:-1]
    return result.stdout.decode("utf-8").splitlines()


def full_checks(event: dict) -> bool:
    if "pull_request" in event:
        return not event["pull_request"].get("draft", False)
    return event.get("ref") == "refs/heads/main" or not event


def changed_paths(event: dict, *, base: str | None = None, root: Path = ROOT) -> list[str]:
    if base:
        return git("diff", "--name-only", "-z", base, "--", root=root)
    if full_checks(event):
        return git("ls-files", "-z", root=root)
    if "pull_request" in event:
        request = event["pull_request"]
        merge_base = git("merge-base", request["base"]["sha"], request["head"]["sha"], root=root)[0]
        return git("diff", "--name-only", "-z", merge_base, request["head"]["sha"], "--", root=root)
    before, after = event.get("before"), event.get("after")
    if before and after and set(before) != {"0"}:
        return git("diff", "--name-only", "-z", before, after, "--", root=root)
    # A new branch or a manual local run has no trustworthy previous baseline.
    return git("ls-files", "-z", root=root)


def selects_all(changed: list[str], subsystem: str, check: str) -> bool:
    configs = set(SHARED_CONFIG)
    if subsystem == BACKEND:
        configs |= {
            "src/backend/pyproject.toml",
            "src/backend/ruff.toml",
            "src/backend/.ruff.toml",
            "src/backend/requirements.txt",
            ".github/workflows/backend-checks.yml",
        }
        configs.add(
            ".github/workflows/pylint.yml" if check == "pylint" else ".github/workflows/ruff.yml"
        )
    else:
        configs |= {
            "src/frontend/package.json",
            "src/frontend/package-lock.json",
            "src/frontend/eslint.config.js",
            "src/frontend/.prettierrc.json",
            "src/frontend/.prettierrc",
            "src/frontend/.prettierrc.yaml",
            "src/frontend/prettier.config.js",
            "src/frontend/.prettierignore",
            ".github/workflows/frontend-checks.yml",
        }
        if check == "size":
            configs.add("tools/check_frontend_size.mjs")
    return bool(configs.intersection(changed))


def quality_paths(changed: list[str], check: str, *, root: Path = ROOT) -> list[str]:
    subsystem = FRONTEND if check.startswith("frontend-") else BACKEND
    kind = check.split("-", 1)[1].removesuffix("-fix")
    paths = git("ls-files", "-z", root=root) if selects_all(changed, subsystem, kind) else changed
    selected = []
    for path in paths:
        if not path.startswith(subsystem) or not (root / path).is_file():
            continue
        suffix = Path(path).suffix
        if subsystem == BACKEND:
            if suffix != ".py":
                continue
            if kind in {"pylint", "size"} and (
                not path.startswith(BACKEND + "src/") or "/migrations/" in path
            ):
                continue
        elif kind == "lint" and suffix not in FRONTEND_SOURCE[:-1]:
            continue
        elif kind == "format" and suffix not in FRONTEND_FORMAT:
            continue
        elif kind == "size" and (
            not path.startswith(FRONTEND + "src/") or suffix not in FRONTEND_SOURCE
        ):
            continue
        selected.append(path)
    return sorted(set(selected))


def affected(changed: list[str], subsystem: str) -> bool:
    return any(path.startswith(subsystem) or path in SHARED_CONFIG for path in changed)


def run_check(check: str, changed: list[str]) -> int:
    subsystem = FRONTEND if check.startswith("frontend-") else BACKEND
    directory = ROOT / subsystem
    if check.endswith("typecheck"):
        if not affected(changed, subsystem) and not selects_all(changed, subsystem, "typecheck"):
            print(f"{check}: no changes to this application.")
            return 0
        command = (
            ["node", "node_modules/vue-tsc/bin/vue-tsc.js", "-b"]
            if subsystem == FRONTEND
            else [
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                "pyproject.toml",
                "src",
            ]
        )
    else:
        files = quality_paths(changed, check)
        if not files:
            print(f"{check}: no applicable changed files.")
            return 0
        print(f"{check}: checking {len(files)} file(s).", flush=True)
        if check == "frontend-size":
            command = ["node", "tools/check_frontend_size.mjs", *files]
            directory = ROOT
        elif check == "backend-size":
            violations = []
            for name in files:
                count = len((ROOT / name).read_text(encoding="utf-8").splitlines())
                if count > 1000:
                    violations.append(f"{name}: {count} lines")
            if violations:
                print("Python files exceeding the 1,000-line maximum:\n" + "\n".join(violations))
            return int(bool(violations))
        else:
            relative = [Path(name).relative_to(subsystem).as_posix() for name in files]
            commands = {
                "frontend-lint": ["node", "node_modules/eslint/bin/eslint.js", "--"],
                "frontend-format": [
                    "node",
                    "node_modules/prettier/bin/prettier.cjs",
                    "--check",
                    "--",
                ],
                "backend-pylint": [
                    sys.executable,
                    "-m",
                    "pylint",
                    "--rcfile=pyproject.toml",
                    "--fail-under=10",
                    "--score=y",
                    "--",
                ],
                "backend-lint": [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    "--output-format=github",
                    "--",
                ],
                "backend-format": [sys.executable, "-m", "ruff", "format", "--check", "--"],
                "frontend-format-fix": [
                    "node",
                    "node_modules/prettier/bin/prettier.cjs",
                    "--write",
                    "--",
                ],
                "backend-lint-fix": [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    "--fix",
                    "--exit-zero",
                    "--",
                ],
                "backend-format-fix": [sys.executable, "-m", "ruff", "format", "--"],
            }
            command = [*commands[check], *relative]
    return subprocess.run(command, cwd=directory, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "check",
        choices=[
            "detect",
            "frontend-lint",
            "frontend-format",
            "frontend-format-fix",
            "frontend-size",
            "frontend-typecheck",
            "backend-lint",
            "backend-format",
            "backend-lint-fix",
            "backend-format-fix",
            "backend-pylint",
            "backend-size",
            "backend-typecheck",
        ],
    )
    parser.add_argument("--base", help="Compare a local working tree against this Git ref.")
    args = parser.parse_args()
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}
    changed = changed_paths(event, base=args.base)
    if args.check != "detect":
        return run_check(args.check, changed)
    output = {
        "frontend": affected(changed, FRONTEND) or selects_all(changed, FRONTEND, "lint"),
        "backend": affected(changed, BACKEND) or selects_all(changed, BACKEND, "lint"),
    }
    print(
        json.dumps(
            {
                **output,
                "mode": "scoped" if args.base or not full_checks(event) else "full",
                "changed_files": changed,
            },
            indent=2,
        )
    )
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            for name, value in output.items():
                stream.write(f"{name}={str(value).lower()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
