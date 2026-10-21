# Prometheus metrics

The backend can expose operational metrics in the Prometheus text format. The endpoint is **off by default**, requires a bearer token when on, and is not served by the public web listener.

## Enabling

| Variable | Default | Meaning |
| --- | --- | --- |
| `METRICS_ENABLED` | `false` | Serve `GET /metrics` from the backend. While `false` the path answers 404. |
| `METRICS_TOKEN` | generated | Bearer token a scraper must send, at least 32 characters (`openssl rand -hex 32`). Leave it empty and the application generates one on first start and saves it as `config/metrics.token` in `APP_DATA_DIR` (mode 0600, kept across restarts and included in volume backups). A configured token shorter than 32 characters stops the backend from starting. |
| `METRICS_LISTEN_PORT` | unset | Production image only: add a scrape-only Nginx listener on this port (not 80 or 443). |
| `PROMETHEUS_MULTIPROC_DIR` | unset | Only when you run several backend worker processes in one container; see below. |
| `APP_VERSION` | `unknown` | Reported in `uta_build_info`. |

## Finding the token

* If you set `METRICS_TOKEN`, you already have it; it is never shown by the application.
* If you left it empty, an administrator can read the generated value once enabled with `GET /api/settings/metrics` (an authenticated, `no-store` response that includes `token`, `token_source`, the path and the configured listen port), or read `config/metrics.token` from the data volume.

Rotating a generated token means deleting `config/metrics.token` and restarting. There is intentionally no way to scrape without a token or to exempt an address from it: addresses can be spoofed or shared behind proxies, and an unauthenticated endpoint would expose route names and job and plugin state to anything that can reach it. Restrict who can reach the port at the network level instead (below).

## Where it is reachable

* `/metrics` is answered by the backend before routing. It is not an API route, is absent from the OpenAPI schema and plugins cannot shadow it. `/api/metrics` does not exist.
* **Development stack:** the backend is only on the Compose `core` network. Point Prometheus at `backend:8000` from that network; do not publish the port to the internet.
* **Production image:** the backend listens on the container's loopback interface and Nginx forwards only `/api/` and static assets, so `/metrics` on ports 80 and 443 is answered 404 by Nginx. To scrape, set `METRICS_LISTEN_PORT=9464` and publish that port only on a network your Prometheus can reach (for example a Docker network shared with Prometheus, without a `ports:` mapping). That listener forwards nothing except `/metrics`, and the token is still required. Do not forward it through an internet-facing reverse proxy.
* Requests without a valid token receive 401; with `METRICS_ENABLED=false` they receive 404.

## Scraping the production deployment

1. Set `METRICS_ENABLED=true` and `METRICS_LISTEN_PORT=9464` on the application container.
2. Put Prometheus on a Docker network shared with that container, and do **not** add a `ports:` mapping for 9464 (publishing it would make it reachable from the host network). Inside the shared network the target is `<service name>:9464`.
3. Give Prometheus the token as a file and reference it with `authorization.credentials_file`.

`examples/metrics/prometheus.yml` has the production job (and the development job as a commented alternative). `examples/metrics/compose.yaml` runs Prometheus and Grafana on a `monitoring` network you attach the application to. If your Prometheus runs on another host, run it behind a VPN or reverse proxy you control; the port is plain HTTP and the token is the only credential.

## Scrape example

`examples/metrics/prometheus.yml` is a scrape configuration and `examples/metrics/alerts.yml` holds starter alert rules. Store the token in a file readable only by Prometheus.

### Grafana

* **Provisioned (recommended):** mount `examples/metrics/grafana/provisioning` at `/etc/grafana/provisioning` and the dashboard file at `/var/lib/grafana/dashboards/unnamed-tracking.json` (`examples/metrics/compose.yaml` does this). The datasource uid is `prometheus`, which the dashboard expects.
* **Manual import:** create a Prometheus data source with uid `prometheus`, then Dashboards, New, Import and upload `examples/metrics/grafana-dashboard.json`.

The dashboard has 16 panels: request rate and latency (p50/p95/p99), slowest routes, 5xx and 429 by route, authentication throttling, sessions, database pool, database and plugin runtime health, plugins by state, plugin runtime latency and errors, job runs and staleness, metadata provider calls and latency, and process CPU and memory.

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
| `uta_job_runs_total` | counter | `job`, `outcome` | Host job ids are labels; plugin and personal jobs share `plugin` and `personal`. |
| `uta_job_run_duration_seconds` | histogram | `job` | Recorded for the host maintenance jobs. |
| `uta_job_last_success_timestamp_seconds` | gauge | `job` | From persisted job state, so it survives restarts. |
| `uta_metadata_provider_calls_total`, `uta_metadata_provider_call_duration_seconds` | counter, histogram | `provider`, `operation`, `outcome` | The built-in providers only. |
| `uta_plugin_runtime_up` | gauge | | Cached health probe of the isolated runtime. |
| `uta_plugins` | gauge | `state` (`installed`, `enabled`, `healthy`, `unhealthy`) | From the runtime's plugin list, cached for 60 seconds. |
| `uta_plugin_runtime_requests_total`, `uta_plugin_runtime_request_duration_seconds` | counter, histogram | `operation`, `outcome` | Host-to-runtime calls, for example `POST /plugins/{id}/start`. Outcomes: `ok`, `rejected`, `unreachable`, `timeout`, `server_error`, `configuration`. |
| `uta_build_info` | info | `version` | |

### Privacy and cardinality

Label values come only from fixed sets: route templates, HTTP methods, status classes, authentication operations, host job ids, built-in provider ids and plugin runtime operation names. Plugin identifiers, usernames, email addresses, titles, URLs with identifiers, client addresses, API keys and tokens are never labels, and the endpoint does not run per-user or library queries. Unknown paths are folded into a single `unmatched` series, so scanners cannot grow the series count.

The plugin runtime is not scraped directly: it runs isolated with only an internal, token-protected interface. The host reports its health and plugin counts and meters every call into it.

## Plugins and metrics

Plugins run in the isolated runtime and cannot add their own series: Plugin API v1 has no metrics capability, and a plugin-supplied label would be an uncontrolled-cardinality and privacy risk. What you can see for plugins today is everything the host observes about them: runtime health (`uta_plugin_runtime_up`), plugin counts by state (`uta_plugins`), every host-to-runtime operation with its latency and outcome (`uta_plugin_runtime_*`), the jobs plugins register (`uta_job_*`, grouped as `plugin`) and the HTTP routes they serve through the host (`uta_http_*`). A plugin metrics capability, with host-defined names, bounded labels and an explicit permission, would be a contract change and is tracked as a follow-up.

## Multiple workers

The shipped images run one backend process, so counters are exact. If you start several workers in one container, set `PROMETHEUS_MULTIPROC_DIR` to an empty, writable directory (cleared on start) before the processes start; `/metrics` then aggregates all workers, and the per-process CPU, memory and GC collectors are omitted (use node or cAdvisor exporters instead). With several replicas, scrape each one and aggregate in Prometheus (`sum by (...)`).

## Retention and alerting guidance

At the default 30 second interval the series count is a few hundred. Keep 15 days of retention unless you need trends; use recording rules for long ranges instead of raising cardinality. Start from `examples/metrics/alerts.yml`: database unreachable, elevated 5xx ratio, slow p95 latency, sustained authentication throttling, plugin runtime down, stale or repeatedly failing jobs and failing metadata providers.

## Not yet covered

Import/export outcomes, upload throughput, cache hit ratios for metadata and artwork, storage totals and migration failures (which happen before the application starts and are reported in the container log) are not exported.
