# Loading, empty, error and retry state audit

Task [#262](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/262). The rule being checked: **a failure must not
look like "nothing there", useful data must not disappear on error, retry must be discoverable and must not double-submit
or reuse a stale result.** This page records what was checked, what was fixed and what is still open. It is an audit of the
flows named below, not of every screen.

## Checklist

| Flow | Request or failure | Finding | Status |
| --- | --- | --- | --- |
| Calendar: watched history, games history, your own entries | any of the three requests fails | games history was cleared to empty and the other two failed silently, so the calendar looked complete | **fixed**: each layer keeps its data, the page names the missing layers and offers **Try again** (`calendarBrowsing.test.ts`) |
| Calendar: months beyond the first window | range request fails or is superseded | new feature; had to avoid the same trap | covered: loading, error with retry, results of an older request ignored; loaded months kept |
| External calendars and occurrence editor | source/window request or save fails; account changes | external data must stay separate from manual entries and retain the last valid snapshot/draft | covered: named missing layer, retry, stale response suppression, cleared account cache, cancelled-event reconciliation and guarded submits (`calendarSources.test.ts`, `calendarSourceEventEditor.test.ts`, `calendarBrowsing.test.ts`) |
| Manual watch logging: title search | library request fails or is slow | failure looked like "no match" | **fixed**: loading, failure with retry, and no-match are distinct; the typed text and chosen day are kept |
| Manual watch logging: submit | double click, stale form | submit was guarded by `manualSaving` already | already covered |
| Metadata search in the game, movie, TV and anime editors | no usable provider | only discovered by clicking | **fixed** ([#254](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/254)): reason and setup link before submitting; server check remains |
| Steam import | details or covers fail for some games | the summary counted "enriched" even without a cover | **fixed** ([#256](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/256)): failed and cover-less games are named and retryable; retries are idempotent |
| Scheduled task intervals | refresh while editing; server refuses a value | edit was saved on every keystroke/change | **fixed** ([#252](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/252)): one draft, explicit save, kept on failure, refresh never overwrites it |
| Upload limits | an environment value locks a field | silent override | **fixed** ([#253](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/253)): source shown, field locked, refused save changes nothing |
| Export / Import tabs | switching mid-import | would have lost a preview if tabs were separate pages | covered: state lives in one component; tested |
| Home (`HomeHub`) | one data source fails | separate loading/error per source already existed | already covered |
| Startup and backend unavailable | backend down | themed unavailable screen with retry already existed | already covered |
| Notification settings | preferences or routing request fails | error shown; stored values kept | already covered; layout changed only ([#273](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/273)) |
| Media picker search, plugin manager popup | out-of-order search; slow or partial plugin popup requests | sequence guards and per-section tolerance already exist | already covered |
| Collection detail (`/games/collections/:name`) | games request fails | showed "Nothing in this collection yet" with no retry | **fixed**: alert with **Retry**, guarded against double starts, no full-page spinner when reloading with data present (`uiStateAudit.test.ts`) |
| Collections index (`/games/collections`) | games request fails | error without retry | **fixed**: **Retry** with in-flight guard and error reset |
| Media lists index (`/media/lists`) | lists request fails | error without retry | **fixed**: **Retry** with in-flight guard |
| Media list detail (`/media/lists/:id`) | id changes while loading; request fails | an older response could overwrite the newer list and the previous list stayed under the new id | **fixed**: sequence guarded, cleared on id change, error has **Retry** |
| Achievement detail notes | navigate to another achievement | a late response filled the wrong achievement's notes | **fixed**: sequence guarded and cleared on change |
| Settings > Users: invitations | invitations request fails | failure was swallowed into an empty list | **fixed**: message with **Retry**, previous invitations kept |
| Settings > Users: user table | users request fails, or a later action errors | any error replaced the whole table and had no retry | **fixed**: table stays when users are loaded, a load failure shows **Retry**, concurrent reloads are ignored |
| Statistics burn-down card | games request fails | the optional card is silently omitted; the main statistics keep their own error state | left |
| Plugin popup UI and logs | request fails | intentionally tolerated; the popup shows the remaining sections | left |

## Not audited yet

Notification pages (they follow the notification overhaul contract), game library list and game detail internals, anime,
movie and TV detail views, the setup page, login and recovery, the upload sections and the Steam and connection dialogs were
only scanned for swallowed-error patterns; no defect was confirmed. Each needs the same four questions asked against its real
requests, so a later change can take them one at a time rather than editing many components at once. Modern, Classic and
narrow-screen checks reuse the existing `ui-state` and `small-button` styles.

## How to check a flow

1. Make each request it issues fail, be slow, return empty, and arrive out of order (a newer request must win).
2. Confirm the screen says which part failed, keeps what it had and the user's draft, and offers retry.
3. Confirm retry cannot submit twice.
4. Add a test that fails if the failure shows as an empty state.

## Screenshots

Captured against the development stack with the games request forced to answer 500 (Modern theme, 800 px pane).

| Page | Before | After |
| --- | --- | --- |
| Collection detail | ![A failed request shown as an empty collection](../assets/ui-state-audit/collection-detail-before.jpg) | ![The failure named, with a Retry button](../assets/ui-state-audit/collection-detail-after.jpg) |
| Collections index | ![Error without a way to retry](../assets/ui-state-audit/collections-index-before.jpg) | ![Error with a Retry button](../assets/ui-state-audit/collections-index-after.jpg) |
