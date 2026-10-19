# Calendar / ICS architecture

The calendar is split between calendar data generation, application-owned manual events, and the public subscription serializer.

## Data flow

```text
Anime / TV / Movie / Game models
            |
            v
  build_calendar_entries()
            |
            +---- calendar preferences
            |
            v
   calendar_feed.py
            |
            v
       _build_ics()
            |
            v
/api/calendar/feed/{token}.ics
```

Manual entries are stored in the `calendar_events` table with a `user_id` foreign key. CRUD operations live in `api/routes/calendar_events.py`. The feed loads only events owned by the user represented by its calendar token.

The calendar token is stored on `users.calendar_token`. It is generated lazily with a cryptographically secure URL-safe random token. Regeneration replaces the stored value, which invalidates the old subscription URL without requiring a separate token-revocation table.

## ICS generation

`src/api/routes/calendar_feed.py` owns the iCalendar serialization. `_ics_escape()` escapes backslashes, commas, semicolons, and line breaks so user-controlled titles and notes cannot create additional iCalendar properties. The feed uses CRLF line endings and emits `VERSION:2.0` and `CALSCALE:GREGORIAN`.

Provider-backed episode UIDs are deterministic from media type, media ID, event kind, and episode number. Manual event UIDs are derived from the persistent `CalendarEvent.id`. These identifiers remain stable between feed fetches, allowing calendar clients to update existing events instead of treating each refresh as a new event.

Episode timestamps are UTC instants. Release dates and manual all-day dates are `VALUE=DATE`. Manual timed entries are floating local times because the current manual-event model stores only `HH:MM`, not an IANA timezone identifier.

The subscription window is 90 days. The feed reuses `build_calendar_entries()` rather than maintaining a second calendar query, so selected airing statuses, title language, release filtering, game filtering, and projected-episode preferences remain aligned with the application calendar.

## External subscriptions

Inbound synchronization is a separate layer: `calendar_sources` holds owner-scoped encrypted URLs and refresh
state; `calendar_source_events` holds validated component snapshots keyed by source, UID and canonical recurrence
identity; `calendar_source_overrides` holds local occurrence/series fields. `calendar_events` and the outbound
serializer remain independent. Imported events are never included in any published feed; an explicit manual copy is.

`features/calendar_fetch.py` uses the existing `helpers/public_http.py` parsing, public-address checks and pinned
connections. HTTPS/443 only, every redirect revalidated, no environment proxy, identity encoding, bounded body,
deadlines and sanitized failure codes. `icalendar` parses components and `python-dateutil` expands the documented
recurrence subset. Parsing and expansion run in bounded workers outside async request execution. PostgreSQL
transaction advisory locks cap remote fetches at two across workers/nodes sharing the database; cancellation keeps
the lock and local worker slot until work exits. DNS uses the operating system resolver: a stuck resolution can
hold a slot beyond the caller's 25-second timeout, but cannot queue unlimited background fetches.

Complete 200 responses reconcile atomically under the source lock. Failed/partial responses and 304 retain the
snapshot. A source nonce/revision rejects stale results after an edit, disable or deletion. Scheduled passes use
the existing maintenance framework with source-specific cadence/backoff, leases and a due-time recheck. Removed
components and associated overrides are pruned after 30 days in bounded batches, including while outbound
subscription fetching is disabled. See the [implemented phase and test matrix](design/ics-subscriptions.md).
