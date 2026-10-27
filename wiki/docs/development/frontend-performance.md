# Frontend asset loading

Production builds keep route views and Settings sections in dynamic imports.
The app shell, vendor runtime, common library data helpers, and library controls
each have a shared bundle. Library cards and top bars share their styles; other
pages and occasional forms retain separate styles. This reduces small requests
without requiring every page's code to execute during startup.

The plugin contribution host loads its declarative form/table renderer only for
an active contribution that needs it. Native contributions use their registered
component. Password controls are not forced into the application shell bundle.

## Controlled transfer comparison

On 27 October 2026, generated production assets were served through a controlled
HTTP harness with six concurrent connections, gzip, one delay per request equal
to the specified RTT, and a shared aggregate bandwidth limit. Each value below
is the median of three runs. All required Games route assets were known at the
start of each run.

| RTT | Previous grouping, 20 Mbps | Selected grouping, 20 Mbps | Previous grouping, 5 Mbps | Selected grouping, 5 Mbps |
|---|---:|---:|---:|---:|
| 100 ms | 448.3 ms | 277.2 ms | 603.8 ms | 464.9 ms |
| 300 ms | 1,246.8 ms | 678.4 ms | 1,362.1 ms | 901.0 ms |
| 700 ms | 2,845.7 ms | 1,478.0 ms | 2,963.0 ms | 1,700.4 ms |
| 1,500 ms | 6,046.4 ms | 3,078.8 ms | 6,163.1 ms | 3,302.0 ms |

Games required 22 JS/CSS requests and 228,755 compressed bytes before this
grouping; the selected build requires 11 requests and 221,987 compressed bytes.
The comparison includes deferring the unused declarative plugin renderer.

Combining the whole application into one script and one stylesheet reduced the
request count to two, but transferred 665,087 compressed bytes. That option was
faster at high RTT in this harness, but took 1,213.0 ms at 5 Mbps / 100 ms RTT,
compared with 464.9 ms for the selected grouping. A single stylesheet with
route-split JavaScript required 17 requests and 294,885 compressed bytes. The
selected grouping preserves smaller route-specific execution and cache units.

These figures measure asset transfer completion, not first paint or usable
content. They exclude HTML transfer, module discovery, connection setup,
JavaScript parsing/execution, API requests and artwork. HTTP/2 multiplexing also
changes the effect of request waves. Browser traces and separate API timings
are needed before claiming equivalent improvements in user-visible loading.
