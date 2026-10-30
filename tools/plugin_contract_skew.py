"""Reconcile the plugin repository's schema snapshots with the host's Plugin API contract.

`tools/check_host_contract.py` in the plugin repository pins its checked-in schema snapshots to
what the host generates. When the host raises PLUGIN_API_CONTRACT_VERSION, the snapshots on the
plugin repository's `main` are one release behind until a plugin pull request refreshes them, and
that pull request cannot pass until the host change is on `main`: a circular merge dependency.

This tool breaks the cycle without weakening the check:

* **same** contract version: nothing is changed; any difference fails the plugin check as before.
* **skew** (plugins declare an older contract than the host): the host must still accept the older
  contract, and every drifted snapshot must be backward compatible with the host schema (see
  `schema_problems`). Compatible drift is reported as warnings and a job summary, and the drifted
  snapshots in the disposable checkout are replaced by the host's schemas so the rest of the plugin
  check (packages, installation, lifecycle) still runs. Incompatible drift fails.
* **ahead** (plugins declare a newer contract than the host): fails.

It only edits the throwaway checkout used by CI and never writes to the plugin repository.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

VERSION_KEY = "x-api-contract-version"
# Snapshots stamped with the whole contract version; they define what the plugins declare.
CONTRACT_STAMPED = (
    "metadata-registration-v1",
    "metadata-request-v1",
    "metadata-response-v1",
)
EXPORTERS = (
    "tools/export_metadata_contract.py",
    "tools/export_notification_contract.py",
    "tools/export_auth_contract.py",
)
# Prose and bookkeeping that cannot change what a document accepts.
IGNORED = frozenset({"description", "title", "examples", "$schema", VERSION_KEY})
# Keys the host may add without narrowing what an older plugin may send or receive.
ADDITIVE = IGNORED | {"default"}
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid contract version: {value!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def host_contract_version(host_root: Path) -> str:
    source = (host_root / "src/backend/src/plugin_api/base_contracts.py").read_text(encoding="utf-8")
    match = re.search(r'^PLUGIN_API_CONTRACT_VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if match is None:
        raise ValueError("PLUGIN_API_CONTRACT_VERSION was not found in the host")
    return match.group(1)


def declared_contract_version(plugins_root: Path) -> str | None:
    """The newest contract version stamped on the plugin repository's snapshots."""
    versions = []
    for name in CONTRACT_STAMPED:
        path = plugins_root / "tools/schemas" / f"{name}.schema.json"
        if path.exists():
            versions.append(json.loads(path.read_text(encoding="utf-8")).get(VERSION_KEY))
    valid = [value for value in versions if isinstance(value, str) and VERSION_RE.fullmatch(value)]
    return max(valid, key=parse_version) if valid else None


def classify(host_version: str, declared: str | None) -> str:
    """`same`, `skew` (plugins older), `ahead` (plugins newer) or `unknown`."""
    if declared is None:
        return "unknown"
    host, plugins = parse_version(host_version), parse_version(declared)
    if plugins == host:
        return "same"
    return "skew" if plugins < host else "ahead"


def schema_problems(old: object, new: object, path: str = "$") -> list[str]:
    """Ways the host schema `new` is not a backward-compatible evolution of the snapshot `old`.

    Allowed: new optional properties, new `$defs`, wider `enum`/`anyOf`/`oneOf`, new defaults and
    changed prose. Everything else is a problem: a removed or changed property, a newly required
    property, a removed enum value, or a constraint that was added or altered.
    """
    problems: list[str] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key, value in old.items():
            if key in IGNORED:
                continue
            if key not in new:
                problems.append(f"{path}: `{key}` was removed")
            elif key in ("properties", "$defs"):
                for name, member in value.items():
                    if name not in new[key]:
                        problems.append(f"{path}.{key}.{name} was removed")
                    else:
                        problems.extend(schema_problems(member, new[key][name], f"{path}.{key}.{name}"))
            elif key == "required":
                added = sorted(set(new[key]) - set(value))
                if added:
                    problems.append(f"{path}: newly required {', '.join(added)}")
            elif key == "enum":
                dropped = [item for item in value if item not in new[key]]
                if dropped:
                    problems.append(f"{path}: enum lost {dropped!r}")
            elif key in ("anyOf", "oneOf"):
                for index, branch in enumerate(value):
                    if all(
                        schema_problems(branch, candidate, f"{path}.{key}[{index}]")
                        for candidate in new[key]
                    ):
                        problems.append(f"{path}.{key}[{index}]: no compatible variant remains")
            else:
                problems.extend(schema_problems(value, new[key], f"{path}.{key}"))
        for key in new.keys() - old.keys():
            if key not in ADDITIVE:
                problems.append(f"{path}: `{key}` was added")
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            problems.append(f"{path}: list length changed")
        for index, (before, after) in enumerate(zip(old, new)):
            problems.extend(schema_problems(before, after, f"{path}[{index}]"))
    elif old != new:
        problems.append(f"{path}: {old!r} changed to {new!r}")
    return problems


def compare_snapshots(
    plugins_root: Path, exported: Path
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Drifted snapshots by name -> compatibility problems (an empty list means compatible).

    Only the metadata snapshots are stamped with the whole contract version. The other documents
    carry the version that introduced them, which the plugin check hard-codes, so the exported
    copy takes the snapshot's stamp (rewritten in `exported`) and cannot register as drift.
    """
    drift: dict[str, list[str]] = {}
    for current in sorted(exported.glob("*.schema.json")):
        snapshot = plugins_root / "tools/schemas" / current.name
        if not snapshot.exists():
            continue
        old = json.loads(snapshot.read_text(encoding="utf-8"))
        new = json.loads(current.read_text(encoding="utf-8"))
        name = current.name.removesuffix(".schema.json")
        pinned = name not in CONTRACT_STAMPED and VERSION_KEY in old
        if pinned and new.get(VERSION_KEY) != old[VERSION_KEY]:
            new[VERSION_KEY] = old[VERSION_KEY]
            text = json.dumps(new, indent=2, ensure_ascii=False)
            current.write_text(text + "\n", encoding="utf-8")
        if old != new:
            drift[name] = schema_problems(old, new)
    incompatible = {name: found for name, found in drift.items() if found}
    return drift, incompatible


def export_host_schemas(host_root: Path, destination: Path) -> None:
    environment = {**os.environ, "PYTHONPATH": str(host_root / "src/backend")}
    for exporter in EXPORTERS:
        subprocess.run(
            [sys.executable, str(host_root / exporter), str(destination)],
            check=True,
            cwd=host_root,
            env=environment,
        )


def host_accepts(declared: str, host_version: str) -> str | None:
    """The host's own reason for rejecting the older contract, or None if it is accepted."""
    sys.path.insert(0, str(Path(__file__).parents[1] / "src/backend"))
    from src.plugin_api.compatibility import plugin_contract_compatibility_reason

    return plugin_contract_compatibility_reason(declared, host_version)


def _annotate(level: str, message: str) -> None:
    print(f"::{level}::{message.replace(chr(10), ' ')}")


def _summary(lines: Iterable[str]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def run(host_root: Path, plugins_root: Path) -> int:
    host_version = host_contract_version(host_root)
    declared = declared_contract_version(plugins_root)
    mode = classify(host_version, declared)
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"mode={mode}\nhost_version={host_version}\n")
    print(f"host contract {host_version}; plugin snapshots declare {declared}: {mode}")
    if mode == "ahead":
        _annotate(
            "error",
            f"The plugin repository declares Plugin API {declared}, newer than this host "
            f"({host_version}). Use a plugin ref that matches this host.",
        )
        return 1
    if mode != "skew":
        return 0  # `same` is checked strictly by the plugin repository; `unknown` gets no leniency.
    assert declared is not None
    reason = host_accepts(declared, host_version)
    if reason is not None:
        _annotate("error", f"Contract skew rejected: {reason}")
        return 1
    with tempfile.TemporaryDirectory() as temporary:
        exported = Path(temporary)
        export_host_schemas(host_root, exported)
        drift, incompatible = compare_snapshots(plugins_root, exported)
        if incompatible:
            for name, problems in incompatible.items():
                _annotate("error", f"{name} is not backward compatible with the host: {problems}")
            _summary(
                ["### Plugin contract skew: incompatible", *(f"- `{name}`" for name in incompatible)]
            )
            return 1
        for name in drift:
            shutil.copyfile(
                exported / f"{name}.schema.json", plugins_root / f"tools/schemas/{name}.schema.json"
            )
    message = (
        f"Plugin contract skew: the plugin ref declares Plugin API {declared} while this host is "
        f"{host_version}. {len(drift)} snapshot(s) differ but are backward compatible; the plugin "
        "repository must refresh its snapshots (tools/export_*_contract.py) for the new contract."
    )
    _annotate("warning", message)
    _summary(
        [
            "### Plugin contract skew (warning)",
            message,
            "",
            "Drifted snapshots (backward compatible, host version used for the rest of this run):",
            *(f"- `{name}`" for name in sorted(drift)),
        ]
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", type=Path, required=True)
    parser.add_argument("--plugins-root", type=Path, required=True)
    arguments = parser.parse_args()
    return run(arguments.host_root.resolve(), arguments.plugins_root.resolve())


if __name__ == "__main__":
    sys.exit(main())
