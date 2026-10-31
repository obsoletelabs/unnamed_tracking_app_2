# Backend Linux test lane (local, Docker)

CI runs the backend tests on Linux with Python 3.12 and PostgreSQL 18
(`.github/workflows/backend-checks.yml`). The backend does not import or test cleanly on Windows
(psycopg wheels, POSIX-only backup and runtime code), so this lane reproduces the CI job locally
with Docker. It does not replace CI and does not change any workflow.

## Requirements

Docker with Compose v2 (Docker Desktop on Windows or macOS). Run the script from Git Bash, WSL or
any POSIX shell.

## Usage

```bash
tools/backend_linux_lane.sh                    # whole backend suite
tools/backend_linux_lane.sh -k identity        # extra arguments go to pytest
tools/backend_linux_lane.sh --shard 2/6        # one CI shard (tools/backend_test_shards.py)
tools/backend_linux_lane.sh --tools            # repository-level tool tests (CI: repository-tool-tests)
tools/backend_linux_lane.sh --plugin-runtime   # plugin runtime tests (CI: plugin-runtime-tests)
tools/backend_linux_lane.sh --shell            # migrated database and a shell in the container
LANE_OUT=/some/dir tools/backend_linux_lane.sh # result folder, default .lane-out (git-ignored)
```

The first run builds the image (a few minutes); later runs reuse the cached dependency layer
until `src/backend/requirements.txt` changes. The database lives on a tmpfs and is removed when
the run ends.

## What it does

Same steps and environment values as the `backend-tests` job:

1. `postgres:18` with user, password and database `test` / `test` / `unnamed_tracking_test`.
2. `python:3.12` image with the pinned `requirements.txt`, `restic`, and the PostgreSQL 18 client
   (from PGDG, so `pg_dump` is not older than the server).
3. The working tree is copied into the container, so the host tree is never written to. A git
   repository holding the release tags is rebuilt there because
   `test_migration_upgrade_matrix.py` runs `git tag` and `git archive`.
4. `alembic upgrade head`, then `pytest` over the selected files.

Results are written to the result folder: `pytest-output.txt` (with `-rA`, one line per test
outcome), `pytest-junit.xml` (usable with `tools/backend_test_shards.py update-timings`) and
`migrations-output.txt`.

## Differences from CI

- One process runs the whole suite instead of several shards, unless `--shard` is used.
- The base image is Debian bookworm, CI uses the current Ubuntu runner image; the PostgreSQL
  client is version 18 here.
- mypy, pylint, ruff, the migration-graph check and the file-size check are not part of this
  lane; run them as described in `AGENTS.md`.
