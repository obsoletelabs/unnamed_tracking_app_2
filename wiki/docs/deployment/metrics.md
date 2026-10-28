# Prometheus metrics

The backend can expose operational metrics in the Prometheus text format. The endpoint is **off by default** in the standalone backend, and is not served by the public web listener. The normal API port requires a bearer token; the dedicated scrape port is restricted by network reachability.

## Summary

* **Production image:** metrics are on by default. A **scrape port** (`9464`, remappable with `METRICS_LISTEN_PORT`) is always present inside the container and needs no token. It is closed unless you publish it, so there is nothing to configure: map the port to a trusted network and point Prometheus at it.
* **Public web ports (80/443):** `/metrics` answers 404 unless you opt in with `METRICS_NGINX_ROUTE_ENABLED=true`, which passes it through to the backend with the bearer token still required.
* **Standalone/development backend:** with `METRICS_ENABLED=true`, port 9464 serves only `/metrics` without a token. The regular API port 8000 still requires the token. Publishing 9464 is an explicit operator choice.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `METRICS_ENABLED` | `true` in the production image, `false` in the development backend | Serves `GET /metrics` from the backend. `false` makes the path answer 404 and removes the scrape port. |
| `METRICS_LISTEN_PORT` | `9464` | Token-free scrape port in both images. For the standalone backend it must be 1–65535, different from API port 8000 and unused. |
| `METRICS_LISTEN_HOST` | `0.0.0.0` | Standalone backend only: interface for the dedicated scrape listener. |
| `METRICS_DIRECT_LISTENER_ENABLED` | `true` in the standalone backend, `false` in the combined image | Start a Python metrics-only listener on the backend event loop. The combined image uses its existing Nginx scrape listener instead. |
| `METRICS_NGINX_ROUTE_ENABLED` | `false` | Production image: opt in to token-protected `/metrics` on the public web ports. |
| `METRICS_TOKEN` | generated | Bearer token (at least 32 characters, `openssl rand -hex 32`). Leave it empty and the application generates one on first start and saves it as `config/metrics.token` in `APP_DATA_DIR` (mode 0600, kept across restarts and included in volume backups). A configured token shorter than 32 characters stops the backend from starting. The scrape port presents this token for you. |
| `PROMETHEUS_MULTIPROC_DIR` | unset | Only when you run several backend worker processes in one container; see below. |
| `APP_VERSION` | `unknown` | Reported in `uta_build_info`. |

## Finding the token

You only need the token for the opt-in public route or the regular backend API port. Neither dedicated scrape port requires it.

* If you set `METRICS_TOKEN`, you already have it; it is never shown by the application.
* If you left it empty, an administrator can read the generated value with `GET /api/settings/metrics` (an authenticated, `no-store` response with `token`, `token_source`, the path and the configured listen port), or read `config/metrics.token` from the data volume.

Rotating a generated token means deleting `config/metrics.token` and restarting. The Nginx configuration is rewritten at start-up with the current token, so the scrape port keeps working.

## Security: who can reach what

* `/metrics` is answered by the backend before routing. It is not an API route, is absent from the OpenAPI schema and plugins cannot shadow it. `/api/metrics` does not exist.
* **The scrape port is not authenticated.** Anything that can connect to it can read route names, job state, plugin counts and runtime health. It is reachable from outside the container only if you publish it, so publish it only to a trusted network (a private Docker network shared with Prometheus, a VPN interface, or `127.0.0.1:9464:9464` for a local scraper). The example compose files show the mapping commented out; do not add it to a service that is directly reachable from the internet. Remap the number with `METRICS_LISTEN_PORT` if 9464 clashes (and change the published mapping to match). The standalone listener accepts only `GET` and `HEAD /metrics`; all other paths return 404.
* **The public route is opt-in** because a mistake there would put the endpoint on the internet-facing ports. With `METRICS_NGINX_ROUTE_ENABLED=true`, `GET /metrics` on 80/443 reaches the backend, which requires the bearer token (401 without it). Put it behind your own access controls if you can.
* The regular backend API listens only on the container's loopback interface in the production image. In the development stack it is on the Compose `core` network; do not publish port 8000.
* Requests to the regular API port without a valid token receive 401; with `METRICS_ENABLED=false` they receive 404 and the standalone scrape listener does not start.

## Scraping the production deployment

1. Publish the scrape port to a network Prometheus can reach, for example by uncommenting `"9464:9464"` in the compose file, or by putting Prometheus on a Docker network shared with the container (no `ports:` mapping needed).
2. Point Prometheus at `<host or service name>:9464` with path `/metrics`. No token is needed.

`examples/metrics/prometheus.yml` has this job (and the standalone backend job as a commented alternative). `examples/metrics/compose.yaml` runs Prometheus and Grafana on a `monitoring` network you attach the application to. If you opted in to the public route instead, use `authorization.credentials_file` with the token and the public address.

## Scraping a standalone backend

Set `METRICS_ENABLED=true` for the backend service and publish/forward only port 9464 to a trusted scraper. The development Compose file shows a commented localhost mapping. Run `curl http://127.0.0.1:9464/metrics` to check the direct port; `curl http://backend:8000/metrics` still needs `Authorization: Bearer <token>`. The listener and API share the same Python event loop and registry, so API requests appear in direct scrapes. No Nginx service is needed.

The direct listener supports one backend worker. `WEB_CONCURRENCY` greater than one fails startup; workers started separately also fail to bind the occupied port. The combined image disables this listener and retains its Nginx implementation. Turning metrics off closes the direct listener. A failed dependency probe leaves other series scrapeable. A failed backend process cannot report its own failure; monitor the scrape target's `up` series as well.

## Scrape example

`examples/metrics/prometheus.yml` is a scrape configuration and `examples/metrics/alerts.yml` holds starter alert rules. Store the token in a file readable only by Prometheus.

### Grafana

* **Provisioned (recommended):** mount `examples/metrics/grafana/provisioning` at `/etc/grafana/provisioning` and both dashboard files at `/var/lib/grafana/dashboards/` (`examples/metrics/compose.yaml` does this). The provisioned Prometheus data source has uid `prometheus` and a 30-second scrape interval, matching the example scraper. The second **Operations** dashboard covers authentication, plugins, notifications, scheduling, backup and integrations.
* **Manual import:** create a Prometheus data source, set its scrape interval to match your scraper, then import `examples/metrics/grafana-dashboard.json` and `examples/metrics/grafana-operations.json` through Dashboards, New, Import. Select the source in each dashboard's **Data source** picker; its uid does not have to be `prometheus`.

The dashboard groups 40 visualizations into an overview and sections for HTTP traffic, authentication, database, plugin runtime, scheduled jobs, metadata providers and process resources. It adds explicit scrape/readiness/dependency status, in-flight requests, unhandled exceptions, database probe duration, job duration, and separate CPU, memory and uptime graphs. The errors graph includes both 5xx responses and the dedicated 429 counter.

Choose a **Deployment** (the Prometheus scrape `job`) and one or more **Instances**. These filters apply throughout, so multiple replicas and unrelated process exporters are not mixed accidentally. **HTTP route** narrows the route-labelled HTTP graphs; the unlabelled in-flight gauge remains instance-wide. Shared database/runtime snapshot gauges are shown per instance instead of summed across replicas. The overview reports the worst measured state among selected instances; missing or incomplete measurements remain unknown instead of showing a healthy state.

Rate/latency panels use Grafana's `$__rate_interval`; set the data source scrape interval correctly so this window includes enough samples. Job-run totals use the selected time range. Counter increases are estimates and can be fractional. Wait for several scrapes after a new installation. Missing error series, infrequent jobs, idle provider histograms and process collectors omitted in multiprocess mode can legitimately show **No data** or gaps. The dashboard does not replace these unknown measurements with zero.

Prometheus reserves `job` for the scrape job and, with its default `honor_labels: false`, renames the application's scheduled-job label to `exported_job`. The dashboard and starter job alerts use that preserved label. Keep the default label-conflict behavior when using these examples; custom relabelling or `honor_labels: true` requires adapting those queries. Job-failure alerts keep the scrape job, instance and application job separate, so unrelated failures do not add up to a false alert.

Validate the starter rules with the same Prometheus version as your deployment:

```bash
cd examples/metrics
python3 check_dashboard.py
docker run --rm -v "$PWD:/work:ro" -w /work --entrypoint promtool prom/prometheus:v2.55.1 check rules alerts.yml
docker run --rm -v "$PWD:/work:ro" -w /work --entrypoint promtool prom/prometheus:v2.55.1 test rules alerts.test.yml
```

The dashboard check reads the actual shipped queries and exercises 429s, distinct application jobs, deployment/instance/route filtering and unknown health on a partially missing replica. It uses Docker by default, or an installed binary with `python3 check_dashboard.py --promtool /path/to/promtool`. The rule fixtures cover distinct scheduled jobs, separate replicas and a real repeated-failure alert with useful job/instance annotations.

## Metrics

All names start with `uta_` except the standard process, platform and GC exporters (`process_*`, `python_*`).

| Metric | Type | Labels | Notes |
| --- | --- | --- | --- |
| `uta_http_requests_total` | counter | `method`, `route`, `status_class` | `route` is the route template such as `/api/games/{game_id}`; unmatched paths are `unmatched`. |
| `uta_http_request_duration_seconds` | histogram | `method`, `route` | Time until the response starts. |
| `uta_http_requests_in_flight` | gauge | | |
| `uta_http_exceptions_total` | counter | `route` | Unhandled exceptions (reported as 500). |
| `uta_http_rate_limited_responses_total` | counter | `route` | Responses with status 429. |
| `uta_auth_rate_limit_decisions_total` | counter | `operation`, `outcome` | Allowed, limited, unavailable, refunded and busy decisions per authentication operation. Sign-in, OIDC, recovery and invitation outcomes are visible per route and status class in `uta_http_requests_total`. |
| `uta_auth_sessions` | gauge | `state` (`active`, `revoked`) | Unexpired browser sessions; refreshed at most every 15 seconds. |
| `uta_database_up`, `uta_database_probe_duration_seconds`, `uta_ready` | gauge | | One cached `SELECT 1` plus two small counts per 15 seconds, regardless of scrape rate. |
| `uta_database_pool_connections` | gauge | `state` (`size`, `checked_out`, `overflow`) | Read from the pool object; no query. |
| `uta_database_connection_wait_seconds`, `uta_database_pool_timeouts_total` | histogram, counter | timeout `reason` (`exhausted`) | Pool acquisition including connection creation; see [performance metrics](metrics-performance.md). |
| `uta_database_query_duration_seconds`, `uta_database_transactions_total` | histogram, counter | query `operation`/`outcome`; transaction `outcome` | SQL cursor time and completed session commit/rollback transitions. |
| `uta_job_runs_total` | counter | `job`, `outcome` | Host job ids are labels; plugin and personal jobs share `plugin` and `personal`. |
| `uta_job_run_duration_seconds` | histogram | `job` | Recorded for the host maintenance jobs. |
| `uta_job_last_success_timestamp_seconds` | gauge | `job` | From persisted job state, so it survives restarts. |
| `uta_metadata_provider_calls_total`, `uta_metadata_provider_call_duration_seconds` | counter, histogram | `provider`, `operation`, `outcome` | The built-in providers only. |
| `uta_cache_entries`, `uta_cache_storage_bytes`, `uta_cache_inventory_up`, `uta_cache_upstream_bytes_total` | gauges, counter | fixed `cache_kind` | Bounded cache inventory and measured upstream bytes; see [performance metrics](metrics-performance.md). |
| `uta_plugin_runtime_up` | gauge | | Cached health probe of the isolated runtime. |
| `uta_plugins` | gauge | `state` (`installed`, `enabled`, `healthy`, `unhealthy`) | From the runtime's plugin list, cached for 60 seconds. |
| `uta_plugin_runtime_requests_total`, `uta_plugin_runtime_request_duration_seconds` | counter, histogram | `operation`, `outcome` | Host-to-runtime calls, for example `POST /plugins/{id}/start`. Outcomes: `ok`, `rejected`, `unreachable`, `timeout`, `server_error`, `configuration`. |
| `uta_build_info` | info | `version` | |

See [response, SQL, cache, authentication and plugin host metrics](metrics-performance.md) for the new lifecycle, byte, disconnect, query and cache series, definitions and PromQL.

### Scheduled work, recovery and integrations

`uta_job_pending{task_class}` is the due work seen on the most recent scheduler
tick, grouped into `core`, `personal` and `plugin`; it is not the number of
running workers. `uta_job_schedule_delay_seconds{task_class}` observes the
persisted due time through dispatch, in seconds, with buckets 0.1, 0.5, 1, 5,
15, 60, 300, 900 and 3600. `uta_job_schedule_outcomes_total{task_class,outcome}`
counts scheduler dispatches (`started`) and adapters returning no running work
(`skipped`). These series reset on process restart. The existing `uta_job_*`
series retain their `exported_job` label after Prometheus renames the client
library's `job` label; new task series use `task_class` instead.

`uta_backup_operations_total{backend,operation,outcome}` records `library_json`
snapshot `create` and immediate `verify` (byte-for-byte JSON readback), with
`succeeded` or `failed`. `uta_backup_operation_duration_seconds{backend,operation}`
uses the same seconds buckets as scheduled jobs. `uta_backup_archive_bytes{backend}`
is the latest checked JSON file size, and
`uta_backup_last_verified_timestamp_seconds{backend}` is the last successful
readback time. These gauges reset at process restart; a missing series means
no verification was observed in this process. `migration_recovery` has a
last-verified timestamp restored from the latest private migration report at
startup; `uta_migration_recovery_status{status}` exposes only `succeeded`,
`failed`, `in_progress` or `unavailable`, with no report paths or archive IDs.
`uta_migration_maintenance_duration_seconds` shows the latest completed
pre-migration attempt's elapsed wall time from its persisted report, including
recovery creation and migration; it is absent when that attempt has no finish time.
Startup failures before the HTTP process starts are visible through the
container/Prometheus target status, not through a live self scrape. Neither
readback check is an independent restore drill; #300 owns that workflow.

`uta_data_operations_total{integration,operation,outcome}` and
`uta_data_operation_duration_seconds{integration,operation}` cover `library_json`
import/export, Playnite observation/inventory and calendar refresh. Streamed
`file_upload` success or size rejection has an outcome counter but no operation
timer. Duration uses the scheduled-job buckets.
`uta_data_records_total{integration,entity,outcome}` counts committed game
import/Playnite results and reconciled calendar components. ICS occurrence and
series components are counted separately; a removed upstream event is a
`removed` outcome and locally preserved overrides are not errors. A Playnite
`unlinked` result is not silently called created. `uta_data_transfer_bytes_total`
counts serialized JSON export bytes before compression and successfully stored
streamed upload bytes. It does not count a rejected upload or claim network
transfer size for calendar/Playnite. `uta_integration_last_success_timestamp_seconds`
currently tracks deployment-wide calendar refresh only. No user, source, URL,
title, filename or content becomes a label. These counters and freshness gauges
reset with the process. The host does not currently expose Playnite note
conflict-copy outcomes, so no such metric is claimed.

For operations panels, useful PromQL includes
`sum by (integration,outcome) (rate(uta_data_operations_total[5m]))`,
`histogram_quantile(0.95, sum by (le,task_class) (rate(uta_job_schedule_delay_seconds_bucket[10m])))`,
and `time() - max(uta_backup_last_verified_timestamp_seconds{backend="library_json"})`.
An absent series is unknown, not zero; check Prometheus `up` and the feature's
enabled state before alerting. Multiple replicas should be scraped separately;
sum counters/rates and use `max` only for latest timestamps.

### Privacy and cardinality

Label values come only from fixed sets: route templates, HTTP methods, status classes, authentication operations, host job ids, built-in provider ids and plugin runtime operation names. Plugin identifiers, usernames, email addresses, titles, URLs with identifiers, client addresses, API keys and tokens are never labels, and the endpoint does not run per-user or library queries. Unknown paths are folded into a single `unmatched` series, so scanners cannot grow the series count.

The plugin runtime is not scraped directly: it runs isolated with only an internal, token-protected interface. The host reports its health and plugin counts and meters every call into it.

## Plugins and metrics

Plugin API 1.1.16 adds the grantable `metrics.record` gateway method. It accepts
only four host-defined event values and publishes the aggregate counter
`uta_plugin_reported_events_total{event}`. Plugin names, account identifiers and
arbitrary labels never enter the registry. See the
[gateway contract](../development/plugin-api-v1.md) for the payload and permission.
The host also reports independently observed runtime health (`uta_plugin_runtime_up`),
plugin state (`uta_plugins`), host-to-runtime operation outcomes and latency
(`uta_plugin_runtime_*`), registered job runs (`uta_job_*`) and routed HTTP traffic
(`uta_http_*`). Plugin-reported events are unverified claims, so compare them with
host-observed series before drawing operational conclusions.

## Multiple workers

The shipped images run one backend process, so counters are exact. The standalone Python scrape listener requires one worker. For deployments using several workers, disable the direct listener and use a separately managed authenticated `/metrics` collector with `PROMETHEUS_MULTIPROC_DIR` set to an empty, writable directory (cleared on start) before the processes start. The per-process CPU, memory and GC collectors are omitted in that mode (use node or cAdvisor exporters instead). With several replicas, scrape each one and aggregate in Prometheus (`sum by (...)`).

## Retention and alerting guidance

At the default 30 second interval the series count is a few hundred. Keep 15 days of retention unless you need trends; use recording rules for long ranges instead of raising cardinality. Start from `examples/metrics/alerts.yml`: database unreachable, elevated 5xx ratio, slow p95 latency, sustained authentication throttling, plugin runtime down, stale or repeatedly failing jobs and failing metadata providers.

## Not yet covered

Independent restore-drill results are not exported because the drill workflow is
not yet implemented (#300). There is no general host automation-rule evaluator;
notification delivery and backlog series measure the existing durable delivery
queue. Cache inventory covers the derived artwork preview and media image
directories; other cache stores remain unmeasured.

## Logging

Requests to `/metrics` are excluded from the backend's access log, so a scrape every few seconds does not flood the console. The Nginx scrape port and the opt-in public route also have their access logs disabled.
