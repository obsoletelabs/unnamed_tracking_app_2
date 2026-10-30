"""Work out the image tags a version tag publishes.

* `v1.2.3` is a stable release: `v1.2.3`, `1.2.3`, `1.2`, and `latest` when no higher stable
  release exists.
* `v1.2.3-beta.4` is a beta of the 1.2.3 line: `v1.2.3-beta.4`, `1.2.3-beta.4`, the per-line
  `1.2.3-beta`, and `latest-beta` when 1.2.3 is the next line to ship (the lowest line above the
  highest stable release that has a beta). Rebuilding an older beta moves nothing.
* Any other prerelease (`v1.2.3-remote-launch.1`) publishes only its own exact tags, so a branch
  preview never moves a shared tag.

Several lines can have betas at once. Each keeps its own `X.Y.Z-beta` tag, and `latest-beta`
follows only the line that is next in the queue.
"""

from __future__ import annotations

import re
import subprocess
import sys

VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z-]+)\.(\d+))?$")
Version = tuple[int, int, int]


def parse(tag: str) -> tuple[Version, str | None, int | None] | None:
    match = VERSION.match(tag)
    if match is None:
        return None
    major, minor, patch, label, number = match.groups()
    return (int(major), int(minor), int(patch)), label, int(number) if number else None


def _text(core: Version) -> str:
    return ".".join(map(str, core))


def tags_for(tag: str, existing: list[str]) -> list[str]:
    """The image tags for `tag`, given every version tag that already exists."""
    parsed = parse(tag)
    if parsed is None:
        return []
    core, label, number = parsed
    known = [p for p in (parse(t) for t in existing if t != tag) if p is not None]
    result = [tag, _text(core) if label is None else f"{_text(core)}-{label}.{number}"]
    if label is None:
        result.append(f"{core[0]}.{core[1]}")
        if not any(c > core for c, lab, _ in known if lab is None):
            result.append("latest")
        return result
    if label != "beta":
        return result
    newer_beta = any(c == core and lab == "beta" and (n or 0) > (number or 0) for c, lab, n in known)
    if newer_beta:
        return result
    result.append(f"{_text(core)}-beta")
    stable = [c for c, lab, _ in known if lab is None]
    waiting = sorted({c for c, lab, _ in known if lab == "beta"} | {core})
    queue = [c for c in waiting if c not in stable and c > max(stable, default=(0, 0, 0))]
    if queue and queue[0] == core:
        result.append("latest-beta")
    return result


def main(argv: list[str]) -> int:
    tag = argv[1]
    existing = subprocess.run(
        ["git", "tag", "--list", "v*"], check=True, capture_output=True, text=True
    ).stdout.split()
    for image_tag in tags_for(tag, existing):
        print(image_tag)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
