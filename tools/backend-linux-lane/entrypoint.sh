#!/usr/bin/env bash
# Runs inside the lane container. Mirrors the steps of the "Backend tests" job in
# .github/workflows/backend-checks.yml (migrate, then pytest over whole test files).
set -euo pipefail

WORK=/work
mkdir -p "$WORK" /out

# Copy the tree (not the host .git, which for a worktree points at a host path).
rsync -a --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '__pycache__' --exclude '.venv' \
  --exclude '.lane-out' \
  /src/ "$WORK"/

# Tests such as test_migration_upgrade_matrix.py read release tags with `git tag` / `git archive`,
# so give the copy a repository that holds the tags and the checked-out files.
cd "$WORK"
git init -q .
git config user.email lane@example.invalid
git config user.name lane
if [ -f /out/repo.bundle ]; then
  git fetch -q /out/repo.bundle '+refs/tags/*:refs/tags/*' || true
fi
git add -A >/dev/null 2>&1
git commit -q -m "lane snapshot" --no-gpg-sign >/dev/null 2>&1 || true

export PYTHONPATH="$WORK/src/backend"
cd "$WORK/src/backend"

echo "== python: $(python --version)"
echo "== postgres server: $(psql "postgresql://test:test@postgres:5432/unnamed_tracking_test" -Atc 'show server_version')"
echo "== pg_dump: $(pg_dump --version)"
echo "== restic: $(restic version | head -1)"

echo "== alembic upgrade head"
alembic upgrade head 2>&1 | tee /out/migrations-output.txt

case "${LANE_MODE:-tests}" in
  shell) exec bash ;;
  tools)
    cd "$WORK"
    python -m pytest tools/tests/test_plugin_acceptance_acquisition.py \
      tools/tests/test_environment_reference.py \
      tools/tests/test_backend_test_shards.py 2>&1 | tee /out/repository-pytest-output.txt
    exit "${PIPESTATUS[0]}" ;;
  plugin-runtime)
    cd "$WORK/src/plugin-runtime"
    PYTHONPATH=. python -m pytest -q tests 2>&1 | tee /out/plugin-runtime-output.txt
    exit "${PIPESTATUS[0]}" ;;
esac

# shellcheck disable=SC2206
extra=(${LANE_PYTEST_ARGS:-})
files=()
if [ -n "${LANE_SHARD:-}" ]; then
  mapfile -t files < <(python3 "$WORK/tools/backend_test_shards.py" files --shard "$LANE_SHARD" --shards "${LANE_SHARDS:?LANE_SHARDS required with LANE_SHARD}")
  echo "== shard $LANE_SHARD of $LANE_SHARDS: ${#files[@]} test files"
fi

set +e
# -rA lists every test outcome; cache_dir keeps pytest's cache out of the tree.
python -m pytest -rA -o cache_dir=/tmp/pytest-cache --junitxml=/out/pytest-junit.xml "${extra[@]}" "${files[@]}" 2>&1 | tee /out/pytest-output.txt
status=${PIPESTATUS[0]}
echo "== pytest exit status: $status" | tee -a /out/pytest-output.txt
exit "$status"
