# Startup and Troubleshooting

The production container keeps startup diagnostics available without turning the startup page into a raw log viewer. The page is intentionally concise: it reports lifecycle state and a short failure explanation, while detailed logs remain operator-facing diagnostics.

## Startup sequence

1. Initialise status and diagnostic files.
2. Start Nginx with the startup configuration.
3. Validate database configuration.
4. Wait for PostgreSQL.
5. Run Alembic migrations (`src/database/migrate.py`). After a normal upgrade the database is
   compared with the models; if tables or columns are missing although the recorded revision
   claims them (a database stamped by an image with a differently ordered history, as in
   [#272](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/272)), the rerun-safe
   history is replayed from the starting migration, keeping all data. A database that is still
   incomplete afterwards stops startup with the missing names.
6. Start Uvicorn on `127.0.0.1:8000`.
7. Wait for `/health`.
8. Select the HTTP, HTTPS, or HTTPS-redirect production Nginx configuration and render TLS certificate paths when required.
9. Validate the selected configuration, copy it to `nginx.conf`, and reload Nginx.
10. Verify the compiled frontend.
11. Monitor the backend process.

## Health semantics

The Docker healthcheck reads the file-backed startup status rather than treating the diagnostic Nginx page as readiness.

`overall=starting` means the container is alive but the application is not ready. `overall=failed` means the operator should inspect the diagnostics. Only `overall=ready` is healthy.

A backend crash after readiness changes the status to `BACKEND_CRASHED` and makes the healthcheck fail while keeping diagnostics available.

## Startup page

The static startup page reports application starting, database state, migration state, backend state, frontend state, application ready, and application failed. Normal startup does not render backend, migration, Nginx, or Docker log output. On failure the spinner is replaced by a failure indicator and a concise diagnostic message.

Nginx serves the startup page over HTTP before checking PostgreSQL. The database
wait uses a 120-second elapsed deadline, with each readiness probe capped at two
seconds, including DNS resolution. A database that remains unavailable moves to
`DATABASE_FAILED` within a few seconds of that deadline while the HTTP diagnostic
listener remains available. API requests return JSON with HTTP 503 and a
`Retry-After: 5` header during startup.

If a reverse proxy only forwards to healthy containers, it can hide these
diagnostics while the application is starting or has failed. Inspect the
container's published HTTP port directly to distinguish proxy routing from an
application startup failure. Embedded HTTPS is activated during the ready
handoff; the pre-readiness diagnostic listener uses HTTP.

![The startup page while PostgreSQL is unavailable on a phone](../assets/database-startup/waiting-390.png)

![Database failure diagnostics remain available after the deadline](../assets/database-startup/failed-1440.png)

These screenshots use the real production image with an intentionally
unresolvable database host. The browser checks confirmed waiting and failure
states at 390px and 1440px, JSON API failures, no horizontal overflow, and a
database failure transition 121.18 seconds after container launch.

Raw logs are intentionally not rendered by default: they are useful for operators but noisy for normal startup. The startup page does not add a reload button or other log-management controls.

## Detailed diagnostics

Use the container logging facilities first:

```text
docker logs <container>
docker logs -f <container>
```

Docker exposes container stdout/stderr through docker logs. The production entrypoint writes lifecycle messages there and Nginx errors are directed to stderr. Detailed backend output is retained at /run/unnamed-tracking/backend.log; migration output is retained at /run/unnamed-tracking/migration.log.

If detailed file contents are needed, use the retained files inside the running container:

```text
docker exec <container> cat /run/unnamed-tracking/backend.log
docker exec <container> cat /run/unnamed-tracking/migration.log
```

They can also be copied out:

```text
docker cp <container>:/run/unnamed-tracking/backend.log ./backend.log
docker cp <container>:/run/unnamed-tracking/migration.log ./migration.log
```

Do not inspect Docker's internal logging-driver files directly.

## Failure states

- `CONFIGURATION_FAILED`
- `DATABASE_FAILED`
- `MIGRATION_FAILED`
- `BACKEND_FAILED`
- `BACKEND_TIMEOUT`
- `FRONTEND_FAILED`
- `BACKEND_CRASHED`

## Failure matrix

Each row names the failing component, whether waiting can help (**retryable**) or the
configuration must change (**configuration**), and the safe first action. The message
column is what the startup page or log actually says; the full, redacted detail stays
in the container logs.

| Component | Class | What you see | First action |
| --- | --- | --- | --- |
| Database unreachable | Retryable | `DATABASE_FAILED`: "PostgreSQL did not become ready within 120 seconds. Check that the database container is running and that POSTGRES_HOST and POSTGRES_PORT point at it." The migration step logs "Database is not reachable after 60s ... usually temporary" | Start the database or fix the host/port, then restart. Nothing else needs repair. |
| Database refuses login or name | Configuration | `MIGRATION_FAILED`; `migration.log` reads "Database configuration error: ... Check POSTGRES_USER, POSTGRES_PASSWORD and POSTGRES_DB". It fails at once instead of waiting a minute. | Correct the credentials or database name. |
| Migration failure | Configuration or schema | `MIGRATION_FAILED`: "A refused login means the database credentials are wrong; a permission error means the database user cannot create tables." `migration.log` ends with "Migration failed (ErrorType): reason ... safe to run again" | Grant the database user schema privileges, or read the reason in `migration.log`; restart. A database made by an older history that cannot be completed stops with the missing table or column names. |
| Unwritable persistent storage | Configuration | `BACKEND_FAILED`; `backend.log` reads "Could not read the persistent Fernet key in ... Mount APP_DATA_DIR as a writable persistent volume" | Mount `APP_DATA_DIR` (default `/data`) as a writable volume owned by the container user. |
| Invalid encryption or configuration | Configuration | `BACKEND_FAILED` ("usually because of invalid configuration (for example SECRET_KEY) or an unwritable APP_DATA_DIR") with `backend.log` naming the setting, for example "SECRET_KEY ... is not a valid Fernet key" | Restore the original key from backup; see [environment variables](../setup/environment-variables.md). Key material is never printed. |
| Provider network failure | Retryable unless `invalid_configuration` | Provider and job results carry a structured code: `timeout`, `unavailable` and `rate_limited` (retry later, with backoff and `Retry-After` honoured) versus `invalid_configuration` / `not_configured` (HTTP 400/401/403 or missing key) | Retryable codes need no action. For `invalid_configuration` fix the provider key in settings. Failures never stop startup. |
| Plugin runtime readiness | Configuration | The plugin runtime health shows the Bubblewrap probe result; an unavailable runtime is never reported as healthy and plugin processes are not started | See [plugin runtime](../development/plugin-runtime.md); plugin events carry redacted lifecycle and stderr lines. |

Failures in log output (for example an unwritable log file) are reported on the
console only; they do not stop startup or request handling.

## Diagnostic endpoints

Inspect:

```text
/_startup/status.json
/_startup/details.txt
```

These are operational diagnostics, not durable log storage.

## TLS failures

TLS is disabled by default and is enabled only when `NGINX_TLS_ENABLED=true`. When enabled, the container uses `readytls.conf` unless `NGINX_TLS_REDIRECT_HTTP=true`, in which case it uses `readytlsredirect.conf`. With both certificate variables empty, an existing `/etc/nginx/tls/tls.crt` + `/etc/nginx/tls/tls.key` pair is used when present; otherwise a self-signed localhost certificate/key pair is generated automatically under `/run/unnamed-tracking/tls`.

Check that:

1. the certificate and private key are mounted at the configured paths;
2. both files are readable by the Nginx master;
3. the certificate chain is in the expected PEM order;
4. the certificate and key match;
5. port 443 is published by the deployment;
6. `AUTH_COOKIE_SECURE=true` is set for an HTTPS public URL.

The renderer reports missing/unreadable files without printing private-key contents. Nginx validation catches malformed or mismatched certificate/key material.

If TLS configuration fails during startup, the diagnostic HTTP listener remains available because the HTTPS configuration is activated only during the ready handoff.

## OIDC and proxy URLs

Nginx forwards `X-Forwarded-Proto`, keeping an outer proxy's value only when that proxy is trusted (`NGINX_REALIP_TRUSTED_PROXIES`), and Uvicorn trusts that header only from the local Nginx hop. This allows request-derived OIDC callback URLs to use `https` when clients connect through embedded TLS.

If another reverse proxy is placed in front of the container, ensure it preserves the external HTTPS scheme and that the deployment’s proxy trust boundary remains limited to the expected internal hop.

If the application frontend loads while setup or authentication requests are
temporarily unavailable, it shows a themed **Backend unavailable** screen. It
rechecks startup after five seconds, retries when connectivity returns, and
offers **Retry connection**. Recovery preserves the requested path and query
without reloading the document. A failed authentication check is retried rather
than treated as a confirmed signed-out response; older responses cannot replace
a newer successful login. A confirmed 401 still leads to ordinary sign-in.

During the initial setup and authentication checks, the frontend shows a neutral
**Loading…** screen. It waits for the confirmed destination before mounting Home,
sign-in, or setup, so Home does not flash ahead of a sign-in redirect. Return
destinations preserve real page paths, queries and fragments. The base page,
sign-in entrypoints and setup are excluded from `return_to`; existing redundant
entry-page queries are removed while other query parameters are preserved.

![Neutral screen while setup and authentication resolve](../assets/startup-routing/loading.jpg)

[Startup recovery validation](../assets/ui-redevelopment/stage-startup-conformance.json)
covers 16 real-backend connection-failure cases across phone/desktop and both
themes, plus automatic recovery.

API requests during startup return HTTP 503 with a JSON explanation and `Retry-After: 5`. They never receive the diagnostic HTML document. The frontend retries temporary startup failures and preserves the requested page.

## Shutdown

Docker stop sends SIGTERM to PID 1. PID 1 forwards shutdown to FastAPI, waits for it, and then asks Nginx to quit. If shutdown behavior is incorrect in a deployment, inspect the container logs for the entrypoint’s shutdown messages.

The production smoke test validates database startup, migrations, compiled frontend/API handoff, authentication, controlled failures and shutdown.

## Secrets

Backend and migration logs pass through credential redaction before private retention and Docker forwarding. Public startup endpoints contain concise lifecycle status and operator log paths, without publishing raw log files.
