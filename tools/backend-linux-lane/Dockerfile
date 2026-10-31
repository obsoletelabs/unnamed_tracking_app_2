# Linux test image for the backend pytest suite. It mirrors the "Backend tests" job in
# .github/workflows/backend-checks.yml: Python 3.12, the pinned backend requirements, and the
# Restic and PostgreSQL client tools that the startup-backup tests run for real.
FROM python:3.12-bookworm

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# pg_dump must not be older than the postgres:18 server it dumps, so take the client from PGDG.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg git restic rsync \
 && install -d /usr/share/keyrings \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-18 \
 && rm -rf /var/lib/apt/lists/*

# Dependencies are installed from the requirements file only, so this layer is cached until the
# pins change.
COPY src/backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install -r /tmp/requirements.txt

COPY tools/backend-linux-lane/entrypoint.sh /usr/local/bin/lane-entrypoint
RUN chmod +x /usr/local/bin/lane-entrypoint
ENTRYPOINT ["/usr/local/bin/lane-entrypoint"]
