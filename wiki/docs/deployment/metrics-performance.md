# Response, SQL and cache performance metrics

These series extend the existing protected `/metrics` endpoint. Authentication,
network restrictions and scrape caching are unchanged. Collection happens at the
existing ASGI, SQLAlchemy and cache boundaries; scrapes do not scan artwork,
query libraries or call metadata providers to generate these measurements.

## HTTP response lifecycle

| Metric | Type / unit | Labels |
|---|---|---|
| `uta_http_response_duration_seconds` | histogram / seconds | `method`, `route`, `response_kind`, `outcome` |
| `uta_http_response_bytes_total` | counter / bytes | `method`, `route`, `response_kind` |
| `uta_http_request_bytes_total` | counter / bytes | `method`, `route` |
| `uta_http_disconnects_total` | counter / requests | `method`, `route`, `phase` |

The existing request counter, in-flight gauge, exception/429 counters and
`uta_http_request_duration_seconds` remain in use. The old timer measures
response-start production; it does not measure body completion. Requests aborted
without a response start have status class `aborted` and no response-start
latency sample. Exceptions before headers retain the existing synthetic 500
response-start observation.

The new timer starts at middleware entry and ends after the final ASGI body send
returns, after final trailers when present, or on the first terminal
disconnect/cancellation/error. It excludes subsequent background-task time.
There is one terminal observation per request. A background exception after a
complete response can increment the existing exception counter without changing
that response's completed outcome.

Fixed label values:

- `method`: GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS, OTHER.
- `route`: registered route template, or `unmatched`; never the raw URL/query.
- `response_kind`: `response` (single-body response), `stream` (multiple-body
  response), `event_stream` (SSE), `file` (ASGI sendfile extension).
- `outcome`: `complete`, `error`, `disconnected`, `cancelled`, `incomplete`.
- `phase`: `before_headers`, `body` (only on disconnect observations).

A disconnect is observed through `http.disconnect`, an `OSError` from ASGI
send, or task cancellation. Each request contributes at most one disconnect
counter increment. Cancellation can also come from a server shutdown or timeout;
the counter alone does not prove a browser disconnected. An application OSError
is an error, not a disconnect. Incomplete applications that return without a final
body/trailer are recorded separately.

Request bytes mean **consumed** body chunks: the middleware neither buffers,
rereads nor forces an unread upload to be consumed. Response bytes mean body
bytes accepted by successful ASGI sends, including partial responses before a
failure. Headers, trailers, HTTP framing and TLS are excluded. Nginx compression
and client receipt happen downstream and are not measured; an application
compression middleware inside this boundary would affect the observed byte
count. A failed send can have delivered an unknowable partial chunk: that chunk
is not guessed. Sendfile-extension bodies are not visible and have no byte
series; their timer ends when ASGI accepts the file command. Normal FileResponse
body chunks are measured normally.

Long-lived SSE/stream durations are not interactive API latency. Completion
histograms only observe a stream when it ends; they do not replace the existing
in-flight gauge. Nginx-served JS/CSS, browser parsing, rendering, image decode,
network RTT and paint are outside the backend boundary. The application's
existing GZipMiddleware runs inside this observer, so negotiated application
gzip body bytes are counted after that compression; later proxy work is not.

## SQL cursor execution

`uta_database_query_duration_seconds` is a seconds histogram labelled by fixed
`operation` (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `WITH`, `OTHER`) and `outcome`
(`ok`, `error`, `cancelled`). It times cursor execution, not pool checkout,
transaction lifetime or result fetching/serialization. Its `_count` is the
execution count, avoiding a parallel counter. Engine hooks are installed once.
SQL text, parameters, table names, accounts and exception messages are never
retained as labels. Comments/unrecognized SQL collapse to `OTHER`.

### Pool and transaction pressure

| Metric | Type / unit | Labels |
|---|---|---|
| `uta_database_connection_wait_seconds` | histogram / seconds | none |
| `uta_database_pool_timeouts_total` | counter / timeouts | `reason=exhausted` |
| `uta_database_transactions_total` | counter / completed transitions | `outcome=commit` or `rollback` |

Pool acquisition timing wraps the application's SQLAlchemy async queue pool. It
includes waiting for a free connection and creating a replacement; it is
separate from cursor execution time. A failed acquisition contributes one
histogram observation and one timeout. The fixed `exhausted` reason denotes
the SQLAlchemy pool timeout, not a PostgreSQL lock timeout.

Transaction transitions are observed on the application's async session core,
after successful commit and actual rollback events. They count session
transactions rather than logical API operations; one request can commit more
than once. Deadlock and retry outcomes are absent until the application owns an
explicit retry policy. Counters reset with the process and should be
aggregated across replicas with `sum by (...) (rate(...))`. The pool histogram
uses the same seconds buckets as SQL execution. No SQL text, table or account
identifier is exported.

```promql
histogram_quantile(0.95, sum by (le) (rate(uta_database_connection_wait_seconds_bucket[5m])))
sum(rate(uta_database_pool_timeouts_total{reason="exhausted"}[5m]))
sum(rate(uta_database_transactions_total{outcome="rollback"}[5m]))
```

## Cache work

| Metric | Type / unit | Labels |
|---|---|---|
| `uta_cache_requests_total` | counter / lookups | `cache_kind`, `outcome` |
| `uta_cache_refresh_duration_seconds` | histogram / seconds | `cache_kind`, `outcome` |
| `uta_cache_evictions_total` | counter / removed entries | `cache_kind`, `reason` |
| `uta_cache_entries` | gauge / entries | `cache_kind` |
| `uta_cache_storage_bytes` | gauge / bytes | `cache_kind` |
| `uta_cache_inventory_up` | gauge / available (0/1) | `cache_kind` |
| `uta_cache_upstream_bytes_total` | counter / bytes read | `cache_kind` |

Kinds are the fixed allowlist `metadata_search`, `artwork_preview`,
`collection_cover`. Unknown kind/outcome inputs are not exported.

- Lookup outcomes: `hit`, `miss`, `stale`, `bypass`, `negative_hit`.
  Fresh metadata, a current resized artwork file, and a matching collage content
  key are hits. An unexpired empty metadata result is a negative hit. Expired
  metadata/outdated preview files are stale. Revoked cache authorization, artwork
  already small enough to serve directly, and custom uploaded collection covers
  bypass the derived cache. Authorization/lifecycle checks are preserved.
- Refresh outcomes: `ok`, `error`, `cancelled`. These time actual metadata
  fetch/merge/store, preview resize/store, and collection collage generation.
  Failed provider responses returned as data are errors. Hits do not start a
  refresh timer. No upstream network call is added by instrumentation.
- Removal reasons: `expiry` (expired metadata replaced), `size_pressure`
  (metadata capacity eviction), `invalidation` (existing previews removed after
  source replacement or media collection cover files removed on deletion). Expired entries merely inspected are not counted as
  removed. These are observed removals, not inventory estimates.

The instrumented caches do not implement stale-while-revalidate. Media poster
cache misses share an active download within each worker, including failures;
later requests can retry. Collection-cover lookups still count each observed hit
or miss, and callers that independently render covers count separate refreshes.
No avoided work is inferred. A conditional HTTP 304 after a preview
lookup is still a local cache hit. Cache lookup counters describe observed
application lookups, not browser memory/disk hits that never reach the server.

The metadata search entry count is updated after each cache mutation. A
background scan, at most once every five minutes, measures derived artwork
previews and cached media images under the application data directory. The scan
stops after 50,000 image entries or two seconds; a failure sets
`uta_cache_inventory_up` to 0 and removes stale entry/byte gauges. An empty
cache reports zero with inventory up. Scrapes themselves do not walk files.
In-memory metadata search is counted per worker; on-disk gauges represent the
shared deployment volume and use `max` aggregation in multiprocess mode.

Upstream bytes are incremented for actual chunks read from built-in metadata
HTTP providers and image fetches, including bytes from a response later rejected
as too large or invalid. A shared in-flight image download is counted once;
callers that find the completed file count as cache hits. No saved-byte estimate
is exported. These caches have no configured byte budget, so there is no budget
series to compare against storage use. Other provider-specific caches and
collection-cover disk inventory remain unmeasured; absence means unknown, not
zero. Cache lookup labels are initialized to known zero event counts; idle hit
ratios remain undefined (0/0). Counters reset on process restart and should be
summed across replicas; shared-volume disk gauges must not be summed.

```promql
sum by (cache_kind) (rate(uta_cache_upstream_bytes_total[5m]))
max by (cache_kind) (uta_cache_storage_bytes) and on (cache_kind) (max by (cache_kind) (uta_cache_inventory_up) == 1)
```

## Buckets, labels and aggregation

HTTP completion and SQL execution bucket bounds, in seconds:
`0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, +Inf`.
Cache refresh bounds: `0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, +Inf`.
Values above the largest finite bucket remain counted; an extreme quantile is
not precise beyond that bound.

Counters/histograms reset on process restart. Use rates/increases to account for
resets. Existing `PROMETHEUS_MULTIPROC_DIR` aggregation applies; clear that
directory when starting the worker group. Across replicas sum rates, and sum
histogram buckets **before** computing quantiles. Do not add or average
per-instance percentiles. Snapshot gauges retain their existing aggregation.

No new application label named `job` is introduced. Use the Prometheus scrape
job/instance to select a deployment. No labels contain users, plugin/provider
identifiers, titles, filesystem paths, raw URLs, query text or raw exceptions. SQL has at most
18 operation/outcome combinations; cache lookups at most 15, refreshes at most
9, removals at most 9. HTTP series are bounded by the existing route table and
the enums above; only observed combinations create histograms. Each observed
HTTP combination adds 13 finite buckets, +Inf, sum/count and the client's
created sample. Monitor active-series growth when adding routes; no per-request
or per-account exemplars are added.

## PromQL and Grafana

The Grafana template adds 14 panels, retaining its deployment, instance and HTTP
route controls. SQL/cache panels are instance-wide and explicitly ignore the
HTTP route filter. Missing measurements remain missing; no dashboard expression
turns unknown cache storage or idle latency into zero. No live Grafana validation
was performed for these added panels.

Ordinary completed API p95 by route:

```promql
histogram_quantile(0.95, sum by (job, instance, route, le) (
  rate(uta_http_response_duration_seconds_bucket{
    response_kind="response", outcome="complete"
  }[10m])
))
```

Cache hit ratio, excluding bypass and including negative hits:

```promql
sum by (job, instance, cache_kind) (
  rate(uta_cache_requests_total{outcome=~"hit|negative_hit"}[10m])
) / sum by (job, instance, cache_kind) (
  rate(uta_cache_requests_total{outcome=~"hit|negative_hit|miss|stale"}[10m])
)
```

SQL execution p95:

```promql
histogram_quantile(0.95, sum by (job, instance, operation, le) (
  rate(uta_database_query_duration_seconds_bucket{outcome="ok"}[10m])
))
```

The added `UnnamedTrackingSlowLibraryResponses` starter alert explicitly selects
GET `/api/game/list` and `/api/game/collection-index`, ordinary completed
responses only. It is a diagnostic threshold to tune for your deployment,
not an SLO or an error-budget calculation. Other routes/streams require their
own eligibility definition before making availability or latency guarantees.

## Authentication flow outcomes

`uta_auth_attempts_total{flow,outcome}` counts terminal local credential checks
(`success`, `invalid_credentials`) and OIDC callbacks (`success`, `failed`,
`cancelled`). OIDC `access_denied` is a cancellation, never a server failure.
The local authentication quota has its own existing decision counter; an
attempt rejected before credential verification is not counted as a sign-in
result. OIDC login initiation is also excluded until its callback arrives.
Each observed callback contributes exactly one terminal result. Counters reset
on process restart and rates should be summed across workers/replicas.

`uta_oidc_stage_duration_seconds{stage,outcome}` times actual `discovery`,
`token`, `claims`, and `account_link` work with the cache refresh bucket bounds
listed above. A stage is counted only if reached; UserInfo can be part of the
claims flow but is not included in the claims timer. `uta_oidc_failures_total`
uses only `provider_unavailable`, `timeout`, `validation`, `verified_email`,
`linking_policy`, and `not_configured`. No issuer, subject, claim, account or
raw exception is a label. `uta_auth_session_transitions_total` counts sessions
created after a successful local/OIDC commit, direct revocations after commit,
and rows removed by expired-session cleanup. Expiry itself is not observed at
its exact wall-clock time; other bulk revocation paths are not included.

```promql
sum by (flow, outcome) (rate(uta_auth_attempts_total[5m]))
histogram_quantile(0.95, sum by (stage, le) (rate(uta_oidc_stage_duration_seconds_bucket[10m])))
```

## Plugin host decisions

`uta_plugin_gateway_decisions_total{capability,boundary,decision,reason}`
counts a host permission decision before dispatch. Capabilities are limited to
the host method table plus `other`; boundaries are `account` or `external_app`;
decisions are `allowed` or `denied`; reasons are `granted`, `principal`,
`unsupported_method`, `capability_mismatch`, or `grant_missing`. A permitted
handler can still fail later; this is a policy metric, not an action success
metric. Plugin IDs, request IDs, user IDs and plugin-supplied method names are
never exported.

`uta_plugin_package_validation_failures_total{failure_class}` records rejected
inspections as `integrity`, `manifest`, `signature`, `publisher_policy`, or
`package`. An unsigned or unknown-publisher package presented for explicit
review is not itself a validation failure. The host's serialized package
transaction lock is measured by `uta_plugin_lifecycle_queue_wait_seconds`;
this is actual install/recovery lock wait, not plugin worker capacity or queue
depth. It uses the short histogram buckets above.
`uta_plugin_lifecycle_operations_total{operation,outcome}` reports install and
update transaction results, plus start/stop/remove/rollback runtime transport
results. These are distinct attempts; a rolled-back update may contribute to
both update and rollback counts. Both label sets are fixed by host code. Like
other counters and histograms, these reset on restart and should be summed
across workers/replicas.

```promql
sum by (capability, reason) (rate(uta_plugin_gateway_decisions_total{decision="denied"}[5m]))
sum by (operation, outcome) (rate(uta_plugin_lifecycle_operations_total[5m]))
```
