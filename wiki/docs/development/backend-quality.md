# Backend quality checks

The rework of [PR250](https://github.com/Rosefall-a/unnamed_tracking_app/pull/250) includes [PR432](https://github.com/Rosefall-a/unnamed_tracking_app/pull/432), which was retargeted and merged into the maintenance branch first. Continued cleanup is published on the [ObsoleteLabs fork branch](https://github.com/obsoletelabs/unnamed_tracking_app_2/tree/fix/pylint-ci-rework).

## CI behavior

Backend tests, plugin runtime tests, migration graph validation, module size, mypy, and Pylint run independently. The reusable Pylint workflow runs once. The final `backend-checks` summary fails when any required job fails or is skipped, preserving the required status name while allowing every diagnostic check to run.

Ruff format, lint, and autofix run independently. Autofix is the only writer, targets the actual same-repository PR head, and checks for a stale head before pushing. A formatting failure does not prevent autofix. Commands piped through `tee` propagate failures, and diagnostic artifacts upload after failures.

Pylint uses the shared configuration and `--fail-under=10`. Its score is not parsed from rounded console output. The physical 1,000-line source limit is checked independently and cannot be bypassed by a Pylint directive.

## Agreed Pylint policy

- Missing module, class, and function docstrings are relaxed globally. Public contracts, security boundaries, and non-obvious business logic still need useful documentation.
- SQLAlchemy and Pydantic data models do not need artificial public methods.
- Ruff retains a 100-character formatting target; Pylint checks a 120-character maximum.
- Correctness checks, including `import-error`, `no-member`, `not-callable`, undefined names, and unused imports, remain enabled globally.
- Existing workflows and declared interfaces may have local complexity exemptions for argument counts, locals, branches, statements, or boolean expressions. These preserve explicit validation and transaction steps without raising global limits.
- Similar route bodies, response shapes, field declarations, and compatibility adapters may have local `duplicate-code` exemptions. Similarity alone does not justify changing an application boundary.

Old blanket file-header disables have been removed. Exceptions use the relevant statement, class, or an explicit disable/enable pair around the affected workflow. A 10/10 score means the code passes this documented policy; it does not mean every complexity or similarity finding has been eliminated.

Broad exception handling remains limited to boundaries with defined recovery behavior, such as external-provider fallbacks, per-entry import failure reporting, plugin isolation, and background retry loops. Inner logic should catch the specific failures it can handle.

## Cleanup and architectural boundaries

The cleanup fixes source issues rather than hiding them behind file-wide rules: fixture registration, shadowed upload arguments, invalid merge-time names, timestamp handling, provider normalization, public permission lookups, session/job lifecycle handling, and typed SQLAlchemy expressions. Compatibility exports use explicit `__all__` declarations.

The games router composes its existing responsibilities from `game_assets`, `game_files`, `game_profiles`, `game_checklists`, and `game_metadata`. Basic and advanced notes share the existing `game_notes` module. Shared route ownership, paths, validation, and history helpers live under `api/routes/utils/games.py`. Route order, authentication dependencies, public parameters, and OpenAPI component identities are preserved. Shared title normalization and Steam CDN URLs reside in the existing title/provider modules.

Plugin infrastructure remains in the host application. Plugin implementations are not copied into the host to satisfy tests. Runtime acquisition, trust, permissions, updates, and gateway access retain the supported public contract and their security boundaries.

The migration repair preserves the existing session-network revision identity and predecessor columns on downgrade. Revision `65acf36995e5` joins the maintenance/main histories; it does not introduce an unrelated schema feature.

## Verification

### Runtime security dependencies

The backend pins Pillow 13.0.0 and certifi 2026.10.19, including the image-decoder and CA-bundle security fixes in those releases. Both backend image builds install pip 26.2.2 before application dependencies, so an older cached Python base cannot retain the vulnerable installer. Rebuild the backend image when upgrading an existing deployment so its installed packages match `requirements.txt`. The upload regressions decode real PNG, JPEG, GIF, WebP, AVIF, BMP, TIFF, HEIF and ICO files; profile-picture checks also exercise HEIC with the existing `pillow-heif` integration.

From `src/backend`, run:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy --config-file pyproject.toml src
python -m pylint --rcfile=pyproject.toml --fail-under=10 src
```

The database-backed backend suite runs against PostgreSQL in CI. With the development stack running:

```bash
docker compose exec -e PYTHONPATH=/app backend python -m pytest tests -q
```

Focused checks cover session persistence and OIDC state/account linking, deployment environment locks and encrypted secrets, media lifecycle and exact statistics, imports/exports, provider fallbacks, plugin authorization, package integrity, and runtime cleanup. Before/after comparisons validate the complete OpenAPI document and selected policy/calculation outputs. PostgreSQL migration replay and production container smoke checks remain part of CI; supplementary SQLite checks do not replace them.

Breaking changes: no public API changes. Individual backend checks have separate result names, and the required `backend-checks` summary covers every backend job, including Pylint.

## Upgrade matrix

`src/backend/tests/test_migration_upgrade_matrix.py` upgrades populated databases from historical schema revisions to the current head on PostgreSQL. Each case runs in its own throwaway schema (`migration_matrix_<random>`, created and dropped by the test), so it cannot touch the shared tables of the test database or a real deployment, and nothing in it runs `docker compose down -v`.

For every starting revision the test migrates an empty schema to that revision, inserts a synthetic row into every table it can fill (three users, plus one row for every other table with real foreign keys back to them), snapshots all rows, upgrades to head and compares:

* every row that existed is still present under the same primary key (and therefore with the same owner and parent references);
* every column that survived keeps its value, except the transformations listed in `TRANSFORMED_COLUMNS`;
* tables that disappear must be listed in `DROPPED_TABLES`;
* no foreign key references a missing row, and `alembic current` reports the head;
* files in a data directory are byte-identical (checksums).

Starting points: the initial schema, an early release, the tip of a divergent branch that a later reconciliation merge joined, the reconciliation of three divergent lines, the pre-identity baseline, the merge after the game identity model and the revision before the head. A further case upgrades in two stages and then repeats the upgrade to cover interruption and double execution. A failure names the table and column only, never row contents. Self-tests prove that a lost row, a changed owner, a dropped table and a changed file are all detected.

Release tags are starting points too. For every `v<major>.<minor>.<patch>` tag (alpha, beta and rc tags included; labelled previews such as `v0.0.5-remote-launch.1` are not releases) the test exports that tag's `src/database/migrations` from Git, builds the schema with the migrations the release shipped, populates it and upgrades it with the current code. This covers histories that were later squashed or rewritten between tags. Only the revision files come from the tag; the Alembic environment script is always the current one, because an old copy can import models that no longer exist. A new release tag is picked up automatically, so no test change is needed when a release is cut. The test is skipped when a checkout has no tags; CI fetches them.

Supported upgrade sources are therefore every tagged release. Untagged builds (branch, `pr-<number>` and `sha-` images) are not supported sources: a database migrated by one of them can be ahead of, or diverge from, the released history, and migrations only move forward.

Intentional transformation recorded today: `notification_deliveries.status` is normalised by `22ff87d4d01c`.

Not covered: databases older than the initial revision, restoring from a deployment backup before an upgrade (see [Migration Recovery](../deployment/migration-recovery.md) for the manual procedure), and several containers racing to migrate (guarded by an advisory lock in `src/database/migrate.py`). When a new migration intentionally rewrites data, add the column to `TRANSFORMED_COLUMNS` with the reason in the same pull request.
