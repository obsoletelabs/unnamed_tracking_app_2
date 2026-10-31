# Development

This page describes the current repository development workflow. The backend and frontend are developed as separate applications and are combined by the production container.

## Repository layout

| Path | Purpose |
|---|---|
| `src/backend/src/` | FastAPI application, configuration, API routes, database models, migrations, and feature code. |
| `src/backend/tests/` | Backend pytest suite. |
| `src/frontend/src/` | Vue application, services, router, state, and views. |
| `src/frontend/` | Vite/Vue package and frontend tooling. |
| `src/docker-container/` | Production Docker image, Compose deployment, Nginx configuration, and startup scripts. |
| `tools/` | Repository-level CI, acceptance, and contract export scripts. Their tests are under `tools/tests/`. |
| `wiki/` | This documentation site. |

## Local development

The repository's development Compose file starts PostgreSQL, the backend, and the Vite frontend:

```bash
docker compose up -d --build
```

The development stack exposes the Vite frontend on port `5173`. The Vite development server proxies `/api` requests to the backend service.

The development Compose file reads `.env`. Copy the repository's `example.env` as a starting point and provide the database/bootstrap values required by the configuration handler.

For a clean database reset, the repository currently uses:

```bash
docker compose down -v
docker compose up -d --build
```

The `-v` flag removes the development PostgreSQL volume, so do not use it when you need to preserve local database data.

## Backend development

Backend code targets Python 3.12.

From `src/backend`:

```bash
mypy --config-file pyproject.toml src
pylint --rcfile=pyproject.toml src
```

Run backend tests against a **separate test database**, alongside the running development
stack. The suite creates, alters and drops schema objects; do not select a database
containing development or production data.

Create a test database once (replace the placeholder with your Compose database user):

```bash
docker compose exec db createdb -U <db-user> unnamed_tracking_test
```

Apply migrations to that database with the normal Compose credentials, then pass its
full connection URL explicitly to pytest. URL-encode special characters in the user
and password:

```bash
docker compose exec -e POSTGRES_DB=unnamed_tracking_test backend alembic upgrade head
docker compose exec -e PYTHONPATH=/app \
  -e TEST_DATABASE_URL=postgresql+psycopg://<db-user>:<db-password>@db:5432/unnamed_tracking_test \
  backend python -m pytest tests -q
```

Outside Docker, set `DATABASE_URL` (or `TEST_DATABASE_URL`) to a dedicated migrated
PostgreSQL database reachable from your host, then run `python -m pytest tests -q`
from `src/backend`. CI already supplies `DATABASE_URL` for its isolated database.

Before collecting tests, pytest disables `.env` discovery and removes inherited
`POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB` components so they cannot
override the test URL. `TEST_DATABASE_URL` takes precedence over an inherited
`DATABASE_URL`, which lets a Compose shell keep its development defaults. Without
either explicit URL, collection stops with a configuration error. Pytest does not
create or migrate the selected database automatically.

The pytest configuration uses automatic asyncio support and the test suite is under `src/backend/tests`.

### Repository-level tool tests

The development container holds only the backend source and tests under `/app`, not the complete repository. Tests of repository-level tools therefore live under `tools/tests/` rather than in the backend suite. `tools/tests/test_plugin_acceptance_acquisition.py` checks that the [plugin lifecycle acceptance runner](plugin-validation.md#repeatable-repository-lifecycle-acceptance) substitutes only its own fixture downloads and passes every other URL to the host's download boundary. CI runs it in the Backend tests job.

With the development stack, run it in a one-off backend container that mounts the checkout read-only. It needs no database connection:

```bash
docker compose run --rm --no-deps -v "$PWD:/repository:ro" -w /repository \
  -e PYTHONPATH=/repository/src/backend --entrypoint python backend \
  -m pytest -q -p no:cacheprovider tools/tests/test_plugin_acceptance_acquisition.py
```

Outside Docker, run it from the repository root with the backend requirements installed and the backend configuration environment set, as CI does:

```bash
PYTHONPATH=src/backend python -m pytest -q tools/tests/test_plugin_acceptance_acquisition.py
```

## Frontend development

The frontend uses Vue, Vite, TypeScript, ESLint, Prettier, and Vitest.

From `src/frontend`:

```bash
npm ci
npm run dev
```

Checks:

```bash
npm run lint
npm run format
npm run typecheck
npm run test
```

Use `npm run format:fix` when formatting needs to be corrected.

For early frontend development/testing, `VITE_USE_MOCK_DATA=true` makes the frontend services use their mock behavior. This variable is consumed directly by Vite and is intentionally outside the backend setup registry.

## Database migrations

The backend uses Alembic.

Generate a migration from the backend environment with:

```bash
docker compose exec backend alembic -c alembic.ini revision --autogenerate -m "describe the schema change"
```

Apply migrations with:

```bash
docker compose exec backend alembic -c alembic.ini upgrade head
```

The backend startup process also applies pending migrations. Keep the migration history on a single head and add a new migration rather than rewriting one that has already been shipped.

For migration changes, test both a fresh database and an existing database where practical.

The main/plugin-manager reconciliation uses `b8c7d6e5f403` to join both published histories into one head. It performs no additional schema operations; the missing predecessor migrations run normally. Continue new migrations from that head. Plugin permission, notification, session, lifecycle, and provider-history migrations use the existing create-if-missing helpers so adoption can safely rerun them without deleting persisted plugin records.

### What happens on start

Both the development backend container and the production image run `python -m src.database.migrate` before the API starts. It waits for the database, takes a lock so two starting containers never migrate at once, then decides what to do from the version the database records:

| Database state | What the runner does |
| --- | --- |
| Empty | Builds everything from the migrations. |
| Version is in this code's history | Runs the migrations newer than it. |
| Version is not in the history, or tables exist with no version | Stops before stamping or replaying. An operator must review the history and rehearse recovery on a restored clone. |
| A known revision claims tables/columns that are missing | Stops; no automatic replay. At head this check runs before invoking Alembic; older recognized revisions are checked after their normal pending upgrades. |

A real failure stops startup with the reason instead of retrying the same error.

See [Migration recovery](../deployment/migration-recovery.md) for read-only inspection,
explicit repair and compatibility limits. Existing published merge points remain supported;
do not rewrite them or introduce competing heads. Every new destructive transformation
needs a PR explanation, populated before/after invariants and a restore rehearsal. Schema
presence alone cannot establish data preservation.

### Writing a migration that is safe to re-run

Because an adopted database is attached to the first migration and then runs every later one, each migration after the baseline must be safe on a database that already has part of what it adds. Use the create-if-missing wrappers in `src/database/migration_helpers.py` (`create_table_if_missing`, `add_column_if_missing`, `create_index_if_missing`, `create_unique_constraint_if_missing`, `drop_index_if_exists`) instead of calling `op.*` directly, and name every constraint explicitly so it can be looked up. For a unique, check or foreign-key constraint without a wrapper, guard `op.create_*` with `has_constraint(table, name)`. Like the other helpers, it checks the table that the following `op.*` call will change, found through the connection's `search_path`. A constraint with the same name on another table or in another schema does not count, so an upgrade into an isolated schema still creates its own constraints and its downgrade can drop them. `tests/test_migrations.py` re-runs the whole chain on the already-migrated test database to catch a migration that is not, and `tests/test_migration_helpers.py` upgrades, adopts and downgrades two schemas side by side.

## Configuration development

Application setup/configuration is registry-driven:

```text
config_registry.py
       |
       v
EnvConfigHandler
       |
       +--> process environment
       +--> .env
       +--> persisted application configuration
       +--> startup-mode defaults
       |
       v
setup/configuration API
       |
       v
generic Vue setup renderer
```

When adding a normal configuration field:

1. Add it to `src/backend/src/core/config_registry.py`.
2. Choose its source ownership: ENV, SETUP, or BOTH.
3. Define type, default, validation, visibility, and secrecy.
4. Add explicit persistence mapping when the setting is application-owned.
5. Add a safe example to `example.env`.
6. Update the wiki's Environment Variables page when it is deployment-facing.
7. Add focused tests.

Do not add a normal environment-backed setup field directly to `Setup.vue`; the registry is the source of truth.

## Production image

The production image is built from:

```bash
docker build -f src/docker-container/Dockerfile .
```

It contains the built Vue frontend, Python backend, Nginx, and startup scripts. The image exposes HTTP on container port 80.

The published deployment Compose file is `src/docker-container/compose.yaml`.

## Before opening a pull request

For backend changes, run the backend tests, mypy, pylint, and migration checks relevant to the change.

For frontend changes, run linting, formatting, type checking, and Vitest.

For schema changes, verify the Alembic graph remains a single head and test migration behavior.

Keep user-facing setup/configuration/deployment changes documented in the wiki.

## Development file polling

Vite keeps polling enabled for Docker Desktop Windows bind mounts, using 500 ms for source changes and 2000 ms for binary assets. Generated dist output is ignored. Longer intervals reduce idle filesystem work at the cost of slightly later hot updates; this does not affect production asset serving. Packaged plugin frontend assets use the host/runtime routes, rather than Vite generated output.

A 31 October 2026 Windows-host bind-mount check used Docker Desktop, Vite 8.2.2 and a Linux Node 24.18.1 browser-test runner. Two 10-second process-CPU windows measured 36.30% before and 18.89% after (one logical CPU = 100%). Browser HMR receipt after Windows-host Vue/TypeScript edits measured 380/382 ms before and 521/522 ms after. These are local development samples, not production performance claims or a representative benchmark. Frontend regression and configuration checks run separately under the Dockerfile Node 22 environment.
