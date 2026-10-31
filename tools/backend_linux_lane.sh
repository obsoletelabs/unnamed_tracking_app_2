#!/usr/bin/env bash
# Run the backend pytest suite the way CI does (Linux, Python 3.12, PostgreSQL 18) from a
# developer machine, including Windows with Docker Desktop. See docs/backend-linux-lane.md.
#
#   tools/backend_linux_lane.sh                      whole backend suite
#   tools/backend_linux_lane.sh -k identity          extra arguments go to pytest
#   tools/backend_linux_lane.sh --shard 2/6          one CI shard (same split as backend-checks.yml)
#   tools/backend_linux_lane.sh --tools              repository-level tool tests
#   tools/backend_linux_lane.sh --plugin-runtime     plugin runtime tests (POSIX-only code)
#   tools/backend_linux_lane.sh --shell              migrated database plus an interactive shell
#   LANE_OUT=/some/dir tools/backend_linux_lane.sh   where results are written (default .lane-out)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode=tests
shard=""
shards=""
pytest_args=()

while [ $# -gt 0 ]; do
  case "$1" in
    --shard) shard="${2%%/*}"; shards="${2##*/}"; shift 2 ;;
    --tools) mode=tools; shift ;;
    --plugin-runtime) mode=plugin-runtime; shift ;;
    --shell) mode=shell; shift ;;
    -h|--help) sed -n '2,11p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) pytest_args+=("$1"); shift ;;
  esac
done

out="${LANE_OUT:-$root/.lane-out}"
mkdir -p "$out"
rm -f "$out"/pytest-* "$out"/migrations-output.txt "$out"/repository-pytest-output.txt "$out"/plugin-runtime-output.txt
# Release tags for the migration upgrade matrix; a worktree's .git cannot be mounted as is.
git -C "$root" bundle create "$out/repo.bundle" --tags >/dev/null 2>&1 || rm -f "$out/repo.bundle"

# Compose wants a Windows-style path for bind mounts when run from Git Bash.
if command -v cygpath >/dev/null 2>&1; then out="$(cygpath -m "$out")"; fi

export LANE_OUT="$out" LANE_MODE="$mode" LANE_SHARD="$shard" LANE_SHARDS="$shards"
export LANE_PYTEST_ARGS="${pytest_args[*]:-}"
export MSYS_NO_PATHCONV=1

compose_file="$root/tools/backend-linux-lane/compose.yaml"
if command -v cygpath >/dev/null 2>&1; then compose_file="$(cygpath -m "$compose_file")"; fi
compose=(docker compose -f "$compose_file")
trap '"${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true' EXIT

"${compose[@]}" build runner
"${compose[@]}" run --rm runner
