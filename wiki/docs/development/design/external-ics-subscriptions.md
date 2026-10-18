# Design: external ICS subscriptions

Status: **proposal for review** (task [#258](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/258), part of
[#248](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/248)). Nothing here is implemented: this task ships no
network fetching and no schema change. It builds on [Calendar / ICS architecture](../calendar-ics.md).

## 1. Problem and non-goals

People keep dates in other calendars (a cinema's film list, a studio's release calendar, a friend's watch party). They want
those dates to appear in the application's calendar next to airings and releases.

**In scope:** per-user *inbound* sources that the server fetches, parses and shows read-only on the calendar.

**Non-goals:** writing back to the remote calendar; CalDAV; two-way sync; changing the outbound feeds
(`calendar_feed.py`) or their per-layer filters; letting a plugin fetch feeds on behalf of the host; importing a feed into
manual events by default.

## 2. Import or subscribe?

| | Import once | Subscribe (recommended) |
| --- | --- | --- |
| What you get | a copy of today's events as manual entries | a live, read-only layer that follows the source |
| Changes upstream | lost | applied on refresh, including cancellations |
| Ownership | the entries become yours and editable | the source owns them; you can hide or turn them into a manual copy |
| Risk | duplicates when imported twice | none to your own entries |

**Recommendation: subscribe first; offer "copy to my entries" per event.** Import is then "subscribe once, copy, remove the
source". This keeps one network path and one reconciliation model.

## 3. Data model (phase 2; additive)

```text
calendar_sources                         calendar_source_events
-----------------                        ----------------------
id (uuid)                                id (uuid)
user_id -> users (cascade)               source_id -> calendar_sources (cascade)
label (<= 80)                            uid (<= 255)            -- the feed's UID
url_encrypted (Fernet, see crypto.py)    recurrence_key (<= 64)  -- "" for a single event, RECURRENCE-ID or occurrence start
url_host (for display only)              starts_on / starts_at / ends_*  -- see section 6
enabled, color_hint                      all_day, title, location (<= 200), note (<= 2000)
refresh_minutes (60..10080, default 360) status: "confirmed" | "tentative" | "cancelled"
etag, last_modified                      sequence, content_hash
last_checked_at, last_success_at         hidden_by_user (bool)
last_error_code, consecutive_failures    first_seen_at, last_seen_at
feed_hash, event_count                   UNIQUE (source_id, uid, recurrence_key)
```

* The URL is a **secret** (many feeds carry a token): it is stored encrypted with the same Fernet key as other
  credentials, never returned by the API (the API returns `url_host` and a `has_url` flag), and never logged.
* Ownership is `user_id` on the source; every query filters by it. Events have no user column; they are only reachable
  through their source.
* Source events are **separate rows from `calendar_events`**. Manual events are never overwritten, merged or deleted by a
  refresh (section 7).
* Migration: one revision creating the two tables and an index on `(source_id, starts_on)`. No data migration; the head
  stays single and linear; downgrade drops the tables.

## 4. API (phase 2)

```text
GET    /api/calendar/sources                    list (no URL), with status and last error
POST   /api/calendar/sources                    {label, url}  -> validates the URL and fetches once
PATCH  /api/calendar/sources/{id}               label, enabled, refresh_minutes, color_hint
DELETE /api/calendar/sources/{id}               removes the source and its events
POST   /api/calendar/sources/{id}/refresh       manual refresh (rate limited, see 5.3)
POST   /api/calendar/sources/{id}/events/{eid}/copy   copy one occurrence into manual events
PATCH  /api/calendar/sources/{id}/events/{eid}  {hidden}
```

`GET /api/calendar` gains an optional `include_sources` (default true) and returns source events as entries with
`kind: "external"`, `source_label`, and `is_projected: false`. The outbound feed never includes them unless a later,
explicit setting says so (default off: re-publishing someone else's calendar is a privacy surprise).

## 5. Fetching

### 5.1 Network policy

Reuse `helpers/public_http.py`, which already pins DNS and rejects non-public addresses; extend it, do not fork it.

| Risk | Rule |
| --- | --- |
| Scheme | `https` only. `http` is refused (feeds can contain private data and a token). `webcal://` is accepted and rewritten to `https://`. |
| Credentials in the URL | `user:pass@` is refused (as `parse_http_url` already does); tokens in the path or query are allowed and treated as secrets. |
| Ports | 443 only. |
| DNS rebinding | resolve once per hop, require **every** address to be public (`is_global`), connect to the pinned address, keep the original host for SNI and certificate verification (what `public_connection` does). |
| Private, loopback, link-local, multicast, cloud metadata (`169.254.169.254`, `fd00:ec2::254`), carrier-grade NAT, IPv4-mapped IPv6, container service names | all fall out of `is_global`; add tests for each, plus numeric/decimal/octal host forms and trailing-dot hosts. |
| Redirects | at most 3; **each** hop is re-validated from scratch (scheme, port, DNS, address); a downgrade to `http` or a redirect to a different port is a failure. |
| Proxy bypass | the client must not honour `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` or `trust_env` (an environment proxy would let a request reach an internal address the application cannot). The fetch runs through the pinned connection, never through a proxy. |
| Timeouts | 5 s connect, 15 s read, **30 s total** wall clock (a slow-drip server cannot hold a worker). |
| Size | stop reading at **5 MB** and fail; reject `Content-Encoding` other than identity or gzip; cap the *decompressed* size, not just the wire size. |
| Content | must parse as iCalendar; `Content-Type` is a hint, not a gate. |
| Concurrency | per-user single flight (reuse the `sync_scope` pattern in `connection_jobs.py`), at most 4 source fetches running server-wide. |

### 5.2 Conditional requests and refresh state

* Send `If-None-Match` / `If-Modified-Since` from the stored `etag` / `last_modified`. `304` updates `last_checked_at` only.
* A `200` is parsed fully **before** anything is written; the whole refresh is one transaction. If parsing fails, the previous
  events stay exactly as they were and `last_error_code` is set (`malformed`, `too_large`, `timeout`, `unavailable`,
  `blocked_address`, `unauthorized` for 401/403, `not_found` for 404/410).
* **Retention of the last valid feed:** a failing source keeps showing its last good events, marked "last updated
  <time>", until the user removes it. An error never empties a calendar.
* Back-off: `consecutive_failures` doubles the interval (cap 24 h); after 14 days of failures the source is shown as
  stale and refreshing pauses until a manual refresh succeeds.

### 5.3 Scheduling

Use the existing jobs loop (`features/jobs.py`) as one job, `calendar_sources`, not a new scheduler: each run processes at
most 5 due sources, oldest first, so a user with many sources cannot starve others. Manual refresh is limited to once a
minute per source and shows the same result states as a scheduled one.

## 6. Parsing and time

Use a maintained iCalendar parser (add one dependency, e.g. `icalendar` with `recurring-ical-events` for expansion);
do not hand-roll RFC 5545.

| Case | Treatment |
| --- | --- |
| `DTSTART;VALUE=DATE` (all-day) | stored as `starts_on`, shown as an all-day entry on that date in the viewer's calendar, never shifted by timezone |
| `DTSTART:...Z` (UTC) | stored as an instant (`starts_at`), shown in the viewer's timezone |
| `DTSTART;TZID=Europe/Paris:...` | resolved with the zone database to an instant; an unknown `TZID` is treated as floating and flagged |
| Floating (`DTSTART:20261024T200000`, no zone) | stored as wall-clock time, shown at that clock time for every viewer (matches how manual timed entries already behave) |
| `DURATION` / `DTEND` | `DTEND` wins; missing both means all-day = one day, timed = zero length |
| Recurrence (`RRULE`, `RDATE`, `EXDATE`, `RECURRENCE-ID`) | expanded only inside a **window of the next 400 days and the previous 31**, at most **500 occurrences per rule** and **5 000 events per source**; overrides (`RECURRENCE-ID`) replace the matching occurrence; `RRULE` with no `COUNT`/`UNTIL` is bounded by the window |
| `STATUS:CANCELLED` | stored as `cancelled` and hidden from the calendar (kept so a re-activation is recognised) |
| Missing `UID` | the event is skipped and counted in the refresh result (`skipped_without_uid`) |
| `SEQUENCE` | an older sequence than stored for the same `(uid, recurrence_key)` is ignored |

Text is plain text: strip control characters, truncate, never render HTML, never follow `URL:` automatically (show it as an
`https`-only link with `rel="noopener noreferrer nofollow"`). Attachments, alarms and attendees are ignored.

## 7. Reconciliation

A refresh computes the new set keyed by `(uid, recurrence_key)` and then:

1. inserts keys not present (`first_seen_at = now`);
2. updates rows whose `content_hash` or `sequence` changed;
3. marks rows missing from the feed as **cancelled-by-source** (not deleted) so a transient omission is reversible, and
   deletes them after 30 days unseen;
4. never touches `hidden_by_user`.

**Duplicates** inside one feed (same UID twice) keep the highest `SEQUENCE`. The same event in two different sources is two
rows (the user chose two sources).

**Conflicts with manual events:** none by construction, because they are different tables. If a source event and a manual
entry share a title and day, both are shown; the UI offers "this is the same as my entry" which sets `hidden_by_user` on
the source event. "Copy to my entries" creates a normal manual event with a pointer to nothing: it is detached, so later
source changes never alter it.

## 8. Privacy and security

* Only the owner sees their sources; admins can see counts and error codes, never URLs or events.
* The URL is encrypted at rest and redacted from logs and error messages (`url_host` only).
* Deleting a source or the account deletes its events (cascade).
* Source text is untrusted: bounded, stripped, escaped on output; ICS injection into the *outbound* feed is impossible
  because external events are not included there by default and `_ics_escape` is used if they ever are.

## 9. Test matrix (written before the feature)

Fixtures live in `tests/fixtures/ics/` and are served by an in-process fake HTTPS server with a controllable resolver.

| Area | Cases |
| --- | --- |
| Updates | new event, changed title/time, higher `SEQUENCE`, lower `SEQUENCE` ignored, event removed upstream (cancelled-by-source, then deleted after 30 days) |
| Duplicates | same UID twice, same UID in two sources, same title and day as a manual entry |
| Cancellations | `STATUS:CANCELLED`, `EXDATE`, cancelled override of one occurrence, re-activation |
| Time | all-day, UTC, `TZID`, unknown `TZID`, floating, DST transitions (spring forward, fall back), leap day, year boundary, DTEND before DTSTART |
| Recurrence | daily/weekly/monthly/yearly, `COUNT`, `UNTIL`, no end, `BYDAY` lists, `EXDATE`, `RDATE`, `RECURRENCE-ID` override, 100 000-occurrence rule is capped |
| Malformed | empty body, not iCalendar, truncated mid-VEVENT, missing `END:VCALENDAR`, binary garbage, huge line, nested `BEGIN`, invalid UTF-8, 6 MB body, gzip bomb |
| Network | `http://`, `webcal://`, userinfo, port 8443, host resolving to `127.0.0.1`, `10.x`, `169.254.169.254`, `::1`, IPv4-mapped IPv6, mixed public+private answers, rebinding between hops, redirect to private, redirect loop, 4 redirects, redirect downgrade, proxy environment variables set, slow-drip server, connect timeout |
| Authentication | 401/403 (`unauthorized`), 404/410, 429 with `Retry-After`, 5xx, `304` |
| State | failure keeps last good events, success after failure clears the error, back-off schedule, manual refresh rate limit, per-user single flight |
| Ownership | another user cannot read, refresh, copy from or delete a source; cascade on user delete |
| Secrets | the URL never appears in API responses, logs, exceptions or the outbound feed |

## 10. Phases

1. **Fetch library** (no schema, no UI): `fetch_ics(url)` with the network policy above and its full network/malformed test
   matrix; a script to try a URL. Reviewable alone.
2. **Storage and API**: migration, endpoints, reconciliation, ownership tests, encrypted URL.
3. **Scheduling**: the jobs-loop adapter, back-off, per-user single flight, manual refresh limits.
4. **UI**: Settings › Calendar › Sources (add, status, refresh, remove), the external layer and its filter chip, "copy to my
   entries", hide.
5. **Docs and rollout**: user guide, administrator note (outbound HTTPS from the server is required), release note.

## 11. Open questions for review

1. Add one parser dependency (recommended) or accept a narrower hand-written subset? *Recommendation: dependency.*
2. Should an administrator be able to disable external sources server-wide (an outbound-network policy switch)?
   *Recommendation: yes, default on, setting in Administration.*
3. Should external events ever be re-published in the outbound feed? *Recommendation: no.*
4. Limit sources per user (5)? *Recommendation: 10, adjustable.*
5. Show events from before today? *Recommendation: 31 days back, matching the window above.*
