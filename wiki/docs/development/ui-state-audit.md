# UI Loading, Empty, Error and Retry State Audit

Audit for issue #262. Status values: **covered** (already correct, no change),
**fixed** (defect confirmed and fixed here, with a regression test in
`src/frontend/src/tests/uiStateAudit.test.ts`), **left** (noted, not changed).

Rule applied everywhere: a failed request must not look like an empty result,
useful data already on screen is kept, a retry control is visible, and a retry
cannot be started twice or let an older response overwrite a newer one.

## Checklist

| Flow | Route / request | Failure case | Status |
| --- | --- | --- | --- |
| Home widgets | `/` (`HomeHub.vue`, per-widget loaders) | one source fails | covered: per-source error and Retry (`retryWidget`) |
| Calendar, export, provider availability, tasks | various | see their own changes | covered by earlier work, not re-audited |
| Metadata/media picker | `useMediaPicker` search | out-of-order, error | covered: sequence guard, `retry()` |
| Plugin manager popup | `PluginManagerSection.openPlugin` | slow/stale plugin switch, partial failure | covered: `popupGeneration` guard, UI/logs/docs failures tolerated individually |
| Collection detail | `/games/collections/:name`, `fetchGames` | request fails | fixed: showed "Nothing in this collection yet" with no retry; now an alert with Retry, guarded against double starts, and no full-page spinner when reloading with data present |
| Collections index | `/games/collections`, `fetchGames` | request fails | fixed: error without Retry; added Retry with in-flight guard and error reset |
| Media lists index | `/media/lists`, `fetchMediaLists` | request fails | fixed: error without Retry; added Retry with in-flight guard |
| Media list detail | `/media/lists/:id`, `fetchMediaListDetail` | id changes while loading; request fails | fixed: an older response could overwrite the newer list and the previous list stayed visible under the new id; now sequence guarded, cleared on id change, error has Retry |
| Achievement detail notes | `/games/:gameId/achievements/:achievementId`, `listGameNoteSummaries` | navigate to another achievement | fixed: a late response filled the wrong achievement's notes; now sequence guarded and cleared on change |
| Settings > Users | `listInvitations` | invitations request fails | fixed: failure was swallowed into an empty invitation list; now a message with Retry, previous invitations kept |
| Settings > Users | `listUsers` | users request fails, or a later action errors | fixed: any error replaced the whole user table and had no Retry; the table now stays when users are loaded, a load failure shows Retry, concurrent reloads are ignored |
| Statistics burn-down | `StatsSection` `fetchGames` | request fails | left: the optional burn-down card is silently omitted; the main stats keep their own error state |
| Plugin popup UI/logs | `fetchPluginUi`/`fetchPluginLogs` `.catch(() => null)` | request fails | left: intentional, the popup shows the remaining sections |

## Not audited

- Notification pages (they follow the notification overhaul contract) and the
  already-fixed calendar, export/import, provider availability and tasks flows.
- Game library list and game detail internals (`useGameLibrary`), anime, movie
  and TV detail views, `Setup.vue`, login and recovery flows, the upload
  sections and the Steam/connection dialogs: only scanned for swallowed-error
  patterns, no defect was confirmed.
- Backend behaviour and plugin-provided UI content.
- Visual checks on Modern, Classic and narrow screens beyond reusing the
  existing `ui-state` and `small-button` styles; screenshots are left to review.
