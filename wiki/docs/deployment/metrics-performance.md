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

## Cache work

| Metric | Type / unit | Labels |
|---|---|---|
| `uta_cache_requests_total` | counter / lookups | `cache_kind`, `outcome` |
| `uta_cache_refresh_duration_seconds` | histogram / seconds | `cache_kind`, `outcome` |
| `uta_cache_evictions_total` | counter / removed entries | `cache_kind`, `reason` |

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
  source replacement). Expired entries merely inspected are not counted as
  removed. These are observed removals, not inventory estimates.

The instrumented caches do not implement stale-while-revalidate or request
coalescing. Concurrent callers that each rebuild/fetch are separate misses and
refreshes; no avoided work is inferred. A conditional HTTP 304 after a preview
lookup is still a local cache hit. Cache lookup counters describe observed
application lookups, not browser memory/disk hits that never reach the server.

This is partial coverage of issue #355. Entry/storage inventory, configured
budgets, persistent byte accounting, all other metadata/artwork caches and
measured upstream/saved bytes are not implemented. Their absence means unknown,
not zero. Cache lookup labels are initialized to known zero event counts;
idle hit ratios remain undefined (0/0).

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
