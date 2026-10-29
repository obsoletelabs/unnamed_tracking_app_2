# Frontend asset loading

Production builds keep route views and Settings sections in dynamic imports.
The app shell, vendor runtime, common library data helpers, and library controls
each have a shared bundle. Library cards and top bars share their styles; other
pages and occasional forms retain separate styles. This reduces small requests
without requiring every page's code to execute during startup.

The plugin contribution host loads its declarative form/table renderer only for
an active contribution that needs it. Native contributions use their registered
component. The native plugin API exposes its password component through a lazy
Vue wrapper with the same props/events. Password controls are not forced into
the application shell bundle.

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
These comparison builds used the local Vite 8.3.5 installation. A separate
production-lockfile build using Vite 8.2.2 confirmed the same 8-script /
3-stylesheet layout, at 216,678 compressed bytes. The RTT table is from the
comparison builds; it is not a timing claim for the production container.
Deferring the native API's password control subsequently reduced that production
footprint to 215,881 compressed bytes, with the same request count. Its rules
are absent from the Games static stylesheet dependency closure. The RTT
comparison predates that final small deferral.

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

## Startup and artwork priorities

The command palette loads on its first opening and remains mounted afterward,
retaining its search cache. The appearance welcome dialog loads only when needed.
Neither is part of the ordinary library shell's initial dependency closure.

Library posters request the standard, prebuilt 400 px width by default. Explicit
thumbnail sizes remain supported. Data Saver or an effective connection type of
`slow-2g`/`2g` selects 240 px; ordinary and unknown connections use 400 px. Smaller
variants are generated and cached on demand, rather than eagerly generating
every size. This avoids the first 240 px resize during normal library loading.

The notification badge reads `/api/notifications/unread-count`, sharing simultaneous
requests from mounted components. Opening the bell fetches ten persisted notices
with `include_summary=false`; scrolling or Load more requests the next page.
These reads do not run release discovery or calculate the full inbox summaries.
The scheduled discovery loop and the full Notification Centre retain discovery.


## Startup contention

Concurrent appearance loads for the same account share their pending request.
Optional sidebar/bell counts and automatic-match banners begin after the initial
paint, allowing the mounted route to issue its data request first. Upload badges
read the recorded count through `/api/media/inbox/count`; the full inbox view
retains filesystem reconciliation for unrecorded files. The count excludes trash
and other accounts and discards late responses after an account change.


## Optional dependency bundles

The shared vendor group contains Vue and Vue Router. QR-code generation,
Markdown rendering, HTML sanitization and virtual-list code follow their actual
importers instead of being forced into every page's initial vendor bundle.
Rich HTML still uses DOMPurify before rendering; this changes bundle placement,
not sanitization. Route and occasional-dialog code splitting remains in place.

In a production-lockfile comparison after merging main on 30 October 2026, the
vendor script fell from 253.91 KB (90.32 KB gzip) to 136.92 KB (51.95 KB gzip).
Games still needs its virtualizer and sanitizer, so its measured initial asset
transfer fell by about 24 KB while adding one script request. This is a transfer
reduction, not a claim that every page becomes faster by a fixed duration.
