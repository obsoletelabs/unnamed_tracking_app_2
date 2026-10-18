# External ICS subscriptions and synchronization

Design proposal for [#258](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/258)
(source: Rosefall #243). **Design only: no remote fetching and no schema change ships with this page.**
Items marked **OPEN QUESTION** need a maintainer decision before implementation. Paths start `src/backend/src/`.

## 1. Problem and current state

* The host only **publishes** a feed: `api/routes/calendar_feed.py` serialises `build_calendar_entries()` plus the
  user's `calendar_events` rows behind `users.calendar_token`.
* `database/models/calendar_event.py` stores manual events as `(user_id, title, event_date, event_time "HH:MM" or
  null, note, media_type, media_id)`. There is no source identity, no timezone, no end time, no recurrence.
* [Calendar / ICS](../calendar-ics.md) already records why an importer is a separate boundary.
* Reusable pieces: `helpers/public_http.py` (`parse_http_url`; `public_addresses` rejects the whole DNS answer if any
  address is non-global; `public_connection` pins the approved addresses and keeps the hostname for Host/TLS/SNI);
  `core/crypto.py` (`encrypt_secret` / `decrypt_secret`, Fernet from `SECRET_KEY`); `features/job_runs.py` (overlap
  lease, pause/cancel, `BACKOFF_SECONDS`, `MAX_RETRIES`) on `job_settings`.
* `public_http` has no redirect following, no response size cap and no proxy handling of its own; those are new here.

## 2. Goals and non-goals

Goals: per-user external ICS sources; source-scoped identity so a refresh updates and never duplicates; keep the
last valid snapshot on any failure; never overwrite or merge into a manual event; bounded fetching.

Non-goals: writing back to a remote calendar; CalDAV or OAuth calendars; changing the outbound feed or its
filters; any plugin implementation (a plugin would use the public Plugin API, not these tables); a full RFC 5545
recurrence engine beyond the stated subset.

## 3. Proposed data model (additive, one migration, single linear head)

`calendar_sources` (owner `user_id` FK, cascade):

| Column | Notes |
| --- | --- |
| `id`, `user_id`, `name` (<=100), `color` | Display only |
| `url_encrypted` | Whole URL encrypted with `encrypt_secret` (ICS URLs often embed a secret). The UI shows only the host |
| `mode` | `subscribe` (default) or `import_once` |
| `enabled`, `refresh_minutes` | Clamped, e.g. 60 to 1440, default 360 |
| `etag`, `last_modified` | Conditional request validators |
| `last_attempt_at`, `last_success_at`, `next_refresh_at` | Epoch ints like the other models |
| `last_status` (`ok`, `not_modified`, `network`, `http_error`, `auth`, `too_large`, `malformed`, `limit`), `last_error` (short, never contains the URL) | Refresh state |
| `consecutive_failures`, `content_sha256` | Backoff and "nothing changed" short-circuit |

`calendar_source_events` (the import snapshot; never the manual table):

| Column | Notes |
| --- | --- |
| `id`, `source_id` (FK cascade), `user_id` | |
| `uid`, `recurrence_id` (null for the base component), `sequence` | Unique `(source_id, uid, recurrence_id)` |
| `title`, `description`, `location`, `url` | Length-capped plain text; no raw HTML stored |
| `starts_at` / `ends_at` (UTC instant) or `start_date` / `end_date`, `time_kind` (`utc`, `zoned`, `floating`, `date`), `tzid` | See section 7 |
| `rrule`, `exdates`, `rdates` | Stored text, expanded on read inside the visible window |
| `status` (`confirmed`, `tentative`, `cancelled`), `removed_at` | See section 6 |
| `last_seen_at` | Last refresh that contained it |

`calendar_events` is **unchanged**. Index `(user_id, starts_at)` for window queries. Downgrade drops both tables.

## 4. API and UI sketch

All routes use `get_current_user` and are scoped by `user_id`; foreign ids are 404.

| Route | Purpose |
| --- | --- |
| `GET/POST /api/calendar/sources` | List; create (validates the URL, performs a **dry-run** fetch, returns a preview count and diagnostics, stores nothing on failure) |
| `PATCH/DELETE /api/calendar/sources/{id}` | Rename, colour, interval, enable; delete cascades the snapshot |
| `POST /api/calendar/sources/{id}/refresh` | Manual refresh, rate limited (e.g. once per 5 minutes per source); returns the state object |
| `GET /api/calendar/sources/{id}/events` | Read-only, for the source detail view |

`build_calendar_entries()` gains an optional external layer, with a per-source toggle in the existing calendar
preferences. Entries carry `source_id` and are visibly distinct (colour, source name). UI: a "Subscriptions"
section in Calendar settings (Modern and Classic) listing each source with last success, last error and a Refresh
button. External events are **excluded from the published ICS by default**, so a subscription cannot be
re-exported through the user's token (**OPEN QUESTION 3**). Inbound synchronization stays separate from outbound feed filters.

## 5. Import versus subscribe

* `subscribe`: scheduled and manual refreshes reconcile the snapshot; deleting the source removes its events.
* `import_once`: one fetch converts occurrences in the feed window into ordinary `calendar_events` rows (no source
  link, no later updates) and then discards the URL. A possible duplicate (same date, time and title) warns but does not block.
* Upgrade path: nothing to migrate; existing manual events and the outbound feed are untouched. There is no
  automatic conversion between modes; the user creates the other kind.

## 6. Reconciliation rules

1. Identity is `(source_id, UID, RECURRENCE-ID)`. The same UID in two sources is two events.
2. A component with a higher `SEQUENCE` (or, on a tie, a changed content hash) replaces the stored row; a lower
   `SEQUENCE` is ignored as stale.
3. Duplicate `(UID, RECURRENCE-ID)` inside one feed: the last one wins and a counter is reported in the refresh detail.
4. `STATUS:CANCELLED` sets `status = cancelled` and hides the event. A UID missing from a **successful, complete**
   feed gets `removed_at` and is hidden; it is hard-deleted after 30 days. Absence is never inferred from a failed,
   truncated or `304` response.
5. `RECURRENCE-ID` overrides replace one occurrence of the base event; `EXDATE` removes occurrences.
6. **Manual events are never modified, merged or hidden by an import.** Same title and time show as two entries.
7. On any error the previous snapshot stays visible, the failure is recorded and the schedule backs off.

## 7. Time handling

* `Z` and `TZID` values become a UTC instant. `TZID` must be a known IANA zone; an unknown one is treated as
  floating and flagged. `VTIMEZONE` blocks are not interpreted beyond the TZID name.
* Floating times stay floating and render in the user's calendar timezone, as manual timed events do today.
* `VALUE=DATE` is an all-day event with an exclusive `DTEND` per RFC 5545.
* Recurrence subset: `RRULE` with `FREQ` DAILY/WEEKLY/MONTHLY/YEARLY, `INTERVAL`, `COUNT`, `UNTIL`, `BYDAY`,
  `BYMONTHDAY`, plus `EXDATE` and `RDATE`. Anything else is stored but shown as a single occurrence with a notice.
  Expansion covers only the visible window (the feed uses 90 days) and at most 366 occurrences per component.

## 8. Security, privacy and abuse

Network policy (a new fetch wrapper in `helpers/` built on `public_http`, not a second HTTP stack):

* **HTTPS only.** `http://` is refused (`webcal://` is rewritten to `https://`). No credentials in the URL or a
  fragment (`parse_http_url` already rejects them). Port 443 only unless an admin allowlists more.
* **Every hop is revalidated.** Redirects are followed manually, at most 3; each URL is re-parsed, re-resolved and
  connected through `public_connection`. A redirect to http, another scheme or a non-public address aborts.
* **DNS rebinding.** Resolve once per hop and connect to the pinned address (existing behaviour); never re-resolve
  between check and connect. The whole answer is rejected if any record is non-global, which covers loopback,
  RFC 1918, link-local and `169.254.169.254`, CGNAT, multicast and IPv4-mapped IPv6. Docker service names resolve
  to private addresses and are therefore rejected.
* **Proxies.** The client ignores `HTTP(S)_PROXY` / `NO_PROXY` (as `trust_env=False` does in
  `features/notification_providers/browser.py`). An environment proxy would move DNS resolution and the public
  check out of the host's control and could reach internal addresses.
* **Limits.** Connect 5 s, total 20 s; response body 2 MiB, streamed and aborted at the cap (`Content-Length`
  checked first, decompressed bytes counted, `Accept-Encoding: identity` unless gzip is handled under the same
  cap); 10 000 components; 500 KiB per property; 1 000 expanded occurrences per source per refresh; at most 10
  sources per user. A stalled body is cut by the total deadline.
* **Parsing.** Untrusted text: unfold lines with a cap, strip control characters, escape on output exactly as
  `_ics_escape()` does, never render `DESCRIPTION` as HTML, show links only if `http(s)`.
* **Credentials.** The URL (which may itself be the credential) is encrypted at rest, never logged, never returned
  by the API after creation (host and a masked tail only) and redacted in error messages. HTTP Basic or header
  authentication is out of scope for phase 1 (**OPEN QUESTION 2**). Restoring a backup needs the original
  `SECRET_KEY`, like every other encrypted secret.
* **Abuse.** Per-user source cap, manual refresh rate limit, global concurrency of 2 fetches, jitter on schedules,
  failure backoff reusing the `job_runs` constants. Fetching never runs in a request handler except the bounded
  dry-run and manual refresh. Fetched data is never sent anywhere else.
* **Retention.** Deleting a source or a user removes its rows (FK cascade); tombstoned events are purged after 30 days.

## 9. Alternatives considered

* **Write imports into `calendar_events`** with an added `source_id`: rejected, a refresh could then touch user data.
* **Fetch in the browser:** blocked by CORS for most feeds and gives no scheduling.
* **Plugin-based importer:** possible later through the Plugin API, but the network boundary and storage must exist
  in the host first; plugins must not get direct access to `calendar_events`.
* **Third-party iCalendar library:** acceptable for parsing and RRULE if licence and size are reviewed
  (**OPEN QUESTION 5**); a hand-written parser is the riskier choice for malformed input.

## 10. Phasing

1. Schema, parser and recurrence subset as pure functions, with the fixture matrix (no network).
2. Fetch wrapper with limits, redirects and an injected-resolver test harness (no routes).
3. Source CRUD, dry-run, manual refresh, reconciliation and the read-only calendar layer.
4. Scheduled refresh on `job_runs`, backoff, UI state, `import_once`.
5. User guide and this page updated to describe what exists.

## 11. Test strategy (deterministic fixtures)

Fixtures are `.ics` text files under `tests/fixtures/ics/`; the fetch layer takes an injected resolver and
transport, so no test touches the network.

| Area | Cases |
| --- | --- |
| Reconciliation | new, unchanged, updated (SEQUENCE up), stale (SEQUENCE down), duplicate UID in one feed, same UID in two sources, cancelled, missing from feed, missing from a truncated feed (not removed) |
| Parsing | malformed property, unterminated `BEGIN:VEVENT`, truncated body, huge property, 10 001 components, CRLF/LF/folded lines, non-UTF-8 |
| Time | UTC, known TZID, unknown TZID, floating, all-day, DST gap and overlap, exclusive `DTEND`, multi-day |
| Recurrence | DAILY/WEEKLY/BYDAY, COUNT, UNTIL, EXDATE, RECURRENCE-ID override, unsupported rule, expansion cap |
| Network | 200; 304 with validators; 301/302 chain; redirect to private/http/other scheme; 4 redirects; DNS answer mixing public and private; rebinding (second resolve differs); metadata IP; IPv6 loopback; proxy env set; oversized body; slow body; 401/403 (status `auth`); 404/5xx; connection reset |
| Ownership | foreign source id 404; user delete cascades; manual events untouched by a refresh; outbound feed excludes external events |
| Migration | upgrade on a populated database, downgrade, single head |

## 12. OPEN QUESTIONS for the maintainer

1. Are a per-user source cap (proposed 10) and a 2 MiB / 10 000-component limit acceptable, and should an admin be
   able to disable external subscriptions deployment-wide?
2. Should phase 1 support only secret-in-URL feeds, or also HTTP Basic / bearer headers (needs an encrypted auth
   column and a stricter log-redaction review)?
3. Should external events ever appear in the user's own outbound feed (proposed default: never)?
4. Should `import_once` exist in phase 1, or only `subscribe`?
5. May a maintained iCalendar parsing dependency be added (for example `icalendar` plus `recurring-ical-events`),
   or must parsing stay in-tree?
6. Is a 30-day tombstone for events removed upstream the right retention?

## 13. Acceptance-criteria mapping

| Issue criterion | Where |
| --- | --- |
| Storage/API ownership, bounded scheduling and manual refresh, import vs subscribe, upgrade plan | Sections 3, 4, 5, 10 |
| Network policy: HTTPS, every redirect/connection, DNS rebinding, blocked ranges, timeouts, limits, proxy bypass | Section 8 |
| Deterministic fixture/test matrix | Section 11 |
| Phases and review questions explicit; nothing shipped | Sections 10, 12; page header |
