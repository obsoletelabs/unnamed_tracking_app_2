"""Decide, once per commit, whether CI runs and which areas it covers.

Every CI trigger (a push or a pull request) reaches the same workflow. Exactly one of them should
do the work for a given commit:

* a pull request runs unless it is a draft;
* a push to main or a tag always runs;
* a push to any other branch runs only when that branch has no ready pull request, because the
  pull request run already covers it (a draft pull request does not run, so its pushes do). Branches without a pull request still get their CI.

The decision and the changed areas are made here, in one cheap job, so jobs that do not apply are
skipped without ever taking a runner. Anything uncertain falls back to running everything.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

AREAS = ("backend", "frontend", "images", "smoke", "recovery", "restore_drill", "docs")

# Files that only the frontend uses; a change confined to them cannot affect backend checks.
BACKEND_IRRELEVANT = ("src/frontend/", "wiki/docs/user-guide/", "wiki/docs/assets/")
# Files the frontend never reads.
FRONTEND_IRRELEVANT = ("src/backend/", "src/plugin-runtime/", "wiki/")

SMOKE_PATHS = (
    "src/backend/",
    "src/frontend/",
    "src/docker-container/",
    "compose.yaml",
    "example-docker-compose.yaml",
    "tools/check_plugin_runtime_deployment.py",
    ".github/workflows/docker-container.yml",
    ".github/workflows/production-runtime-smoke.yml",
    ".github/workflows/ci.yml",
)
RECOVERY_PATHS = (
    "tools/backup_deployment.py",
    "tools/deployment_backups/",
    "tools/check_deployment_backups.py",
    "tools/check_migration_backups.py",
    "tools/backup_acceptance/",
    "tools/tests/test_deployment_backups.py",
    "src/backend/",
    "src/plugin-runtime/",
    ".github/workflows/deployment-backups.yml",
    ".github/workflows/ci.yml",
)
RESTORE_DRILL_PATHS = (
    "src/backend/src/database/restore_drill.py",
    "src/backend/src/database/startup_backup.py",
    "src/backend/tests/test_restore_drill.py",
    "src/backend/tests/test_migration_upgrade_matrix.py",
    ".github/workflows/restore-drill.yml",
    ".github/workflows/ci.yml",
)
DOCS_PATHS = ("wiki/", ".github/workflows/docs.yml", ".github/workflows/ci.yml")

# `Plugin repository integration` (plugin-integration.yml) is a separate workflow, so it asks this
# module for its own scope instead of using AREAS. Paths that can change how real plugins behave
# against the host run the heavyweight real-worker, browser and Jellyfin acceptance ("heavy").
PLUGIN_HEAVY_PATHS = (
    "src/plugin-runtime/",
    "src/backend/src/plugin_api/",
    "src/backend/src/api/routes/plugin_manager/",
    "src/backend/src/api/routes/plugin_permissions.py",
    "src/backend/requirements.txt",
    "src/frontend/package.json",
    "src/frontend/package-lock.json",
    "tools/check_plugin_",
    "tools/plugin_conformance.py",
    "tools/plugin_contract_skew.py",
    "tools/run_strict_plugin_acceptance.sh",
    ".github/workflows/plugin-integration.yml",
)
# Frontend plugin and media-watch-link code (but not its unit tests) that the browser capture drives.
PLUGIN_FRONTEND = re.compile(r"^src/frontend/(?!.*/tests/).*(plugin|watchlink)", re.IGNORECASE)
# Published schemas and plugin documentation only need the cheap contract comparison.
PLUGIN_CONTRACT_PATHS = (
    "wiki/docs/assets/plugin-",
    "wiki/docs/development/plugin-",
    "wiki/docs/development/metadata-providers.md",
    "tools/export_",
)


@dataclass(frozen=True)
class Decision:
    run: bool
    reason: str


def _branch(ref: str) -> str | None:
    return ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else None


def decide(
    event_name: str,
    event: dict,
    *,
    has_open_pull_request: Callable[[str], bool | None],
) -> Decision:
    """Whether this trigger should do the work. `has_open_pull_request` returns None when unknown."""
    if event_name == "pull_request":
        if event.get("pull_request", {}).get("draft"):
            return Decision(False, "draft pull request")
        return Decision(True, "pull request")
    ref = str(event.get("ref", ""))
    if ref.startswith("refs/tags/") or ref == "refs/heads/main":
        return Decision(True, "main or tag")
    branch = _branch(ref)
    if branch is None:
        return Decision(True, "not a branch push")
    covered = has_open_pull_request(branch)
    if covered:
        return Decision(False, "covered by the open pull request run")
    # An unknown answer runs CI: a duplicate is better than a branch nobody checked.
    return Decision(True, "branch without an open pull request" if covered is False else "unknown")


def _only_under(files: Iterable[str], prefixes: tuple[str, ...]) -> bool:
    return all(path.startswith(prefixes) for path in files)


def _touches(files: Iterable[str], prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefixes) for path in files)


def areas(files: list[str] | None, *, event_name: str, branch_push: bool) -> dict[str, bool]:
    """Which parts of CI apply. `files` is None when the change set is unknown: run everything."""
    everything = {name: True for name in AREAS}
    if files is None or not files:
        return everything
    result = {
        "backend": not _only_under(files, BACKEND_IRRELEVANT),
        "frontend": not _only_under(files, FRONTEND_IRRELEVANT),
        "images": True,
        "smoke": event_name == "pull_request" and _touches(files, SMOKE_PATHS),
        "recovery": not branch_push and _touches(files, RECOVERY_PATHS),
        "restore_drill": event_name == "pull_request" and _touches(files, RESTORE_DRILL_PATHS),
        "docs": _touches(files, DOCS_PATHS),
    }
    return result


def plugin_integration_scope(
    event_name: str, event: dict, files: list[str] | None
) -> dict[str, bool]:
    """What `Plugin repository integration` does: `run` (any of it) and `heavy` (real workers,
    browser, Jellyfin and PWA acceptance). Everything runs on main, tags, manual runs and when the
    change set is unknown; a pull request that touches nothing plugin-related skips it."""
    ref = str(event.get("ref", ""))
    full = event_name == "workflow_dispatch" or ref.startswith("refs/tags/") or ref == "refs/heads/main"
    if full or not files:
        return {"run": True, "heavy": True}
    heavy = any(path.startswith(PLUGIN_HEAVY_PATHS) or PLUGIN_FRONTEND.match(path) for path in files)
    return {"run": heavy or _touches(files, PLUGIN_CONTRACT_PATHS), "heavy": heavy}


def _gh(*arguments: str) -> str | None:
    try:
        done = subprocess.run(
            ["gh", *arguments], check=True, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout


def open_pull_request(repository: str, branch: str) -> bool | None:
    owner = repository.split("/", 1)[0]
    out = _gh(
        "api",
        "-X",
        "GET",
        f"repos/{repository}/pulls",
        "-f",
        f"head={owner}:{branch}",
        "-f",
        "state=open",
        "--jq",
        "[.[] | select(.draft | not)] | length",
    )
    if out is None or not out.strip().isdigit():
        return None
    return int(out.strip()) > 0


def changed_files(repository: str, event_name: str, event: dict) -> list[str] | None:
    """The real set of changed files from the GitHub API (no checkout needed); None if unknown."""
    if event_name == "pull_request":
        number = event.get("pull_request", {}).get("number")
        if not isinstance(number, int):
            return None
        out = _gh(
            "api",
            "--paginate",
            f"repos/{repository}/pulls/{number}/files",
            "--jq",
            ".[] | .filename, (.previous_filename // empty)",
        )
        # The API lists at most 3000 files; a change that large simply runs everything.
        files = out.split() if out is not None else None
        return None if files is None or len(files) >= 3000 else files
    before, after = event.get("before"), event.get("after")
    if not before or not after or set(str(before)) == {"0"}:
        return None  # a new branch has no baseline
    out = _gh(
        "api",
        f"repos/{repository}/compare/{before}...{after}",
        "--jq",
        ".files[] | .filename, (.previous_filename // empty)",
    )
    files = out.split() if out is not None else None
    # The compare endpoint truncates at 300 files; treat that as unknown.
    return None if files is None or len(files) >= 300 else files


def plugin_integration_main(event_name: str, repository: str, event: dict) -> dict[str, str]:
    """Outputs for plugin-integration.yml: a draft pull request, or a branch push that its pull
    request run covers, does nothing; otherwise the scope follows the changed files."""
    if event_name in ("push", "pull_request"):
        decision = decide(
            event_name, event, has_open_pull_request=lambda b: open_pull_request(repository, b)
        )
        if not decision.run:
            return {"run": "false", "heavy": "false", "reason": decision.reason}
    files = None if event_name == "workflow_dispatch" else changed_files(repository, event_name, event)
    scope = plugin_integration_scope(event_name, event, files)
    reason = "unknown change set" if not files else f"{len(files)} changed files"
    return {"run": str(scope["run"]).lower(), "heavy": str(scope["heavy"]).lower(), "reason": reason}


def main() -> int:
    event_name = os.environ["GITHUB_EVENT_NAME"]
    repository = os.environ["GITHUB_REPOSITORY"]
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    if "--plugin-integration" in sys.argv[1:]:
        outputs = plugin_integration_main(event_name, repository, event)
    else:
        outputs = gate_outputs(event_name, repository, event)
    for key, value in outputs.items():
        print(f"{key}={value}")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in outputs.items())
    return 0


def gate_outputs(event_name: str, repository: str, event: dict) -> dict[str, str]:
    decision = decide(
        event_name, event, has_open_pull_request=lambda b: open_pull_request(repository, b)
    )
    outputs: dict[str, str] = {"run": str(decision.run).lower(), "reason": decision.reason}
    if decision.run:
        files = changed_files(repository, event_name, event)
        selected = areas(files, event_name=event_name, branch_push=event_name != "pull_request")
    else:
        selected = {name: False for name in AREAS}
    outputs.update({name: str(value).lower() for name, value in selected.items()})
    return outputs


if __name__ == "__main__":
    sys.exit(main())
