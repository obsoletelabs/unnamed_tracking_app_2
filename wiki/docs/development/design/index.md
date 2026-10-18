# Design proposals

These pages are **proposals for review**. Each answers a design task from the
[Rosefall follow-up queue (#248)](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/248). Completing a design task
ships a reviewed document only: no schema change, no remote fetching, no new default and no rename.

| Proposal | Task | One-line outcome |
| --- | --- | --- |
| [External ICS subscriptions](external-ics-subscriptions.md) | #258 | subscribe (read-only) to remote calendars, with an SSRF-safe fetch and a full test matrix; six phases |
| [AFK time](afk-time.md) | #264 | an optional, opt-in `afk` counter next to playtime, never subtracted by default |
| [Book tracking](book-tracking.md) | #265 | a fourth vertical slice (work, optional editions, reading sessions); Open Library first |
| [Untracked media discovery](untracked-media-discovery.md) | #266 | an opt-in Home widget that never adds anything; franchise gaps first |
| [Tag catalogue](tag-catalogue.md) | #267 | keep arrays; add a per-user alias layer, after measuring need |
| [Project name and logo](branding-decision-brief.md) | #268 | inventory and brief; no rename, no asset replacement |

Each page ends with the questions that need a decision before implementation starts.
