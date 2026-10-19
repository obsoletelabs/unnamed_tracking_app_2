# Calendar & ICS

## Subscribe to your calendar

Unnamed Tracking App provides a per-user iCalendar (`.ics`) feed. Calendar applications can subscribe to the URL without an application login because the URL contains a secret calendar token.

In the **Calendar** view, choose **Subscribe** to generate or display your feed URL. Copy that URL into your calendar application's subscription/calendar-by-URL feature.

### One calendar or several

**Subscribe** lets you pick what to subscribe to. **Everything** is the combined feed that has always been offered, and
its link does not change. You can instead subscribe to separate calendars so each can be shown, hidden or coloured
on its own in your calendar app:

| Choice | Feed path | Contains |
| --- | --- | --- |
| Everything | `/api/calendar/feed/<token>.ics` | all layers and your own entries |
| Anime | `/api/calendar/feed/<token>/anime.ics` | airing episodes and releases of anime |
| TV shows | `/api/calendar/feed/<token>/tv.ics` | airing episodes and premieres |
| Movies | `/api/calendar/feed/<token>/movie.ics` | release dates of movies you plan to watch |
| Games | `/api/calendar/feed/<token>/game.ics` | game release dates (when **Game releases** is on) |
| My entries | `/api/calendar/feed/<token>/manual.ics` | only the entries you added yourself |

Every link uses the same secret token and your saved calendar preferences (estimated episodes, hidden games, airing
statuses). Event IDs are the same in the combined feed and in the per-layer feed, so a subscriber never sees a
duplicate. **Regenerating** replaces the token, which ends *every* old link at once.

!!! note "Colors"
    Each per-layer calendar carries a *suggested* color (`COLOR` and `X-APPLE-CALENDAR-COLOR`), but Google Calendar,
    Apple Calendar and Outlook decide for themselves how, or whether, to use it. Set the color in your calendar app
    if the suggestion is ignored.

### Regenerating the feed URL

The feed URL is a bearer secret. Anyone who has it can read the calendar for that user. If it has been exposed, use the Calendar view's **Regenerate** action. The previous URL immediately stops working and a new token is generated.

Do not paste a feed URL into public issue reports, screenshots, chat rooms, or source control. Prefer HTTPS when the application is accessed over an untrusted network.

## Browsing the calendar

**Week starts on** can be any day of the week. It is saved with your account and is available both under
**Settings → Calendar** and in the calendar's own toolbar. Month and week layouts follow it, including across year
boundaries and leap days.

The calendar opens with the next 90 days loaded. Browsing further ahead (up to about a year) fetches just the
month or week you open, once, as a bounded range, so a distant month never pulls the whole future at once. Only
dates that are really known are shown that far out: a movie or show with a release date, plus each show's later
episodes as dashed **estimates**. Airing times are confirmed for the next episode of a show only, so an empty
distant month does not mean nothing will air, and the calendar says so. If a month fails to load you get a **Try
again** button and everything already loaded stays on screen. The past comes from your history, not from a request
per month.

## Logging something you watched

**Log Watched** adds a *history entry* and nothing else. It never changes a title's watched progress or status; use the
title's own page for that. Search your library for the title (the search says when it is still loading, when loading
failed and can be retried, and when nothing matches). For an episode entry you can type exactly **which episodes**, for
example `3, 5-7`, in any order: the entry is saved as "Episodes 3, 5-7" with a count of four, and out-of-order watching is
never turned into "the first four episodes". Leave the box empty to log just a number of episodes. A movie is always one
viewing. The form shows a sentence describing exactly what saving will do.

If the title is not in your library yet, **Search and add it** takes you to the normal add flow. The day you chose is kept,
and the form reopens on that day when you come back to the calendar.

## What the feed contains

- upcoming movie releases for movies in **Plan to Watch**;
- upcoming first-air dates for TV and anime in **Plan to Watch**;
- upcoming episode airings for TV and anime in the selected calendar airing statuses;
- optional upcoming game releases, controlled by **Game releases** and **Hide games** calendar preferences;
- user-created manual calendar events.

Provider-backed media entries use the same **90-day forward window** as the application calendar. The calendar query also keeps a one-day look-back for recently aired/released media. User-created manual events are exported independently of that media window, so a reminder can remain in a subscription even when it is outside the provider-backed 90-day range.

Projected future episodes are estimates based on the stored airing cadence. They are included when **Show estimated** is enabled; disabling that preference removes only projected episodes from the feed. The provider-confirmed next episode remains included. This matches the application calendar's distinction between confirmed and dashed estimated entries.

## Dates and time zones

Provider-supplied episode times are exported as UTC iCalendar timestamps (`Z`). Calendar applications convert those instants to the viewer's local time zone.

Release/premiere dates are date-only events. They do not have a real time-of-day. The application internally anchors these dates at noon UTC when constructing its calendar data, while the ICS feed emits them as `VALUE=DATE`, so subscribers receive an all-day event on the intended calendar date.

Manual all-day entries are exported as all-day `VALUE=DATE` events. Manual timed entries are stored as the user's local clock time and exported as a floating iCalendar time without a time-zone identifier.

## Importing another ICS feed

External `.ics` importing is **not implemented by this feature**. A separate design issue, **#243**, tracks the future importer architecture. The current calendar model is designed around application-owned events and per-user manual entries, while an external importer would require a separate source/synchronization model.

A safe server-side importer would need persistent source ownership, credentials, refresh scheduling, ETag/Last-Modified handling, UID/upsert rules, cancellation/deletion semantics, timezone conversion, malformed-feed behavior, request timeouts/size limits, and SSRF protection for user-supplied URLs. The application currently does not have a shared outbound-calendar-fetching security boundary for those requests, so remote ICS fetching is intentionally deferred rather than accepting arbitrary URLs.

## Unavailable calendar settings

While **Hide all games layers** is on, **Game releases** and **Games history** are disabled with a note saying why and how to turn them back on. Their saved choices are not changed and apply again when you turn off **Hide all games layers**. The note is also announced to screen readers.
