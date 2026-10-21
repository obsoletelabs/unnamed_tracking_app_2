# Wiki page https://obsoletelabs.github.io/unnamed_tracking_app_2/

Still a WIP, but new features are decently uptodate


# Running the API

docker compose down
docker compose up --build
docker compose -f example-docker-compose.yaml up --build

Use the bottom Compose file for the production container and the standard file for development.
`docker compose down -v` destroys named database/plugin volumes. Take and rehearse a
[full deployment backup](wiki/docs/deployment/backups.md) before deliberately removing them.

docker compose run --rm backend


# checks
## Backend checks
cd src/backend
mypy --config-file pyproject.toml src
pylint --rcfile=pyproject.toml src

## Backend tests
The tests use the running database, so start the stack first.

```bash
docker compose exec -e PYTHONPATH=/app backend python -m pytest tests -q
```

Tests of repository-level tools (`tools/tests`) need the complete checkout rather
than the backend container's `/app`, so they run in a one-off container:

```bash
docker compose run --rm --no-deps -v "$PWD:/repository:ro" -w /repository \
  -e PYTHONPATH=/repository/src/backend --entrypoint python backend \
  -m pytest -q -p no:cacheprovider tools/tests/test_plugin_acceptance_acquisition.py
```

## Frontend checks
cd /src/frontend
npm run lint
npm run format # if this fails run npm run format:fix
npm run typecheck
npx vitest run

# running the wiki
run `mkdocs serve -a 0.0.0.0:999` inside the wiki folder (requires sudo on linux)


# Recommended VS Code extensions
- Ruff by charliermarsh

# Database updates

```bash
docker compose exec backend alembic -c alembic.ini revision --autogenerate -m "your changes here eg add playtime to game table"
```

The backend upgrades recognized database histories on startup, taking a verified local recovery
point first (see the [migration recovery guide](wiki/docs/deployment/migration-recovery.md)). Unknown histories
or missing schema stop startup for [operator review](wiki/docs/deployment/migration-recovery.md).
Write new migrations so they are
safe to run twice (use the helpers in
`src/backend/src/database/migration_helpers.py`), and keep the history a single
line. See the wiki's
[Development > Database migrations](wiki/docs/development/index.md#database-migrations).

# Metadata keys

Movie and TV details come from TMDB and OMDb, games from IGDB. Enter the keys
as an administrator under Settings > Metadata Sources, or set them in `.env`
(`TMDB_API_KEY`, `OMDB_API_KEY`, `TVDB_API_KEY`, `IGDB_CLIENT_ID`,
`IGDB_CLIENT_SECRET`). A key set in `.env` wins and shows as locked in Settings,
and nothing ships with the app.

# Importing and exporting

Settings > Export / Import can bring in a MyAnimeList export (anime), a
Letterboxd export (movies) and an IMDb ratings or watchlist CSV (movies and TV
shows). Each import shows what it would add or change first, and for a title
you already have you choose between keeping it as it is and using the file's
data. It can also restore movies, TV shows and anime from the app's own export,
and export everything as a CSV.

# Scheduled jobs

Recurring work is listed under Settings > Tasks. Each job can be switched on or
off, given its own interval (any whole number of minutes within the range shown
there) and run by hand. There are two:

- **Airing episode check** starts on, every 30 minutes. It adds a numbered row
  for each newly aired episode of a show you follow. It only asks a provider
  about a show once its next episode is due (plus a daily look for a moved
  schedule), and asks AniList about up to 50 shows in one request, so most runs
  do almost nothing.
- **Media refresh** starts off. It fills in missing episode titles and images
  and corrects a show whose episode count is wrong.

Jobs are registered in `src/backend/src/features/jobs.py`.
     
## Security

This project is not currently hardened for direct public-internet exposure. Keep the API behind an appropriate network boundary and do not expose it directly to the public internet.
