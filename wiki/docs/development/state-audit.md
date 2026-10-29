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
| First-run setup (`/setup`) | registry, status or password-policy request fails | rendered the full wizard with no sections and an enabled **Finish setup**, which would try to create an administrator from empty fields | **fixed** ([#334](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/334)): only the failure and **Try again** are shown; `submit()` refuses without a loaded configuration; a failed re-read after a successful save no longer reports "Setup failed" (`uiStateAuditAuth.test.ts`) |
| Accept invitation, reset password | the availability check fails (offline, 5xx) | reported as "unavailable, contact the administrator" with the form hidden | **fixed**: a failed check is told apart from a disabled feature and offers **Try again**; the status services now throw instead of returning `false` |
| Steam wishlist and Steam tags switches | preferences request fails | the hard-coded default was shown as the stored choice and stayed clickable | **fixed**: the switch is locked until the value is read, with **Retry** (`uiStateAuditSteam.test.ts`) |
| Steam sign-in dialog | connection request fails on open | a "Connect Steam" sign-in form was offered, hiding an existing connection or pending sign-in | **fixed**: only the error and **Try again** until the connection is known |
| Statistics burn-down card | games request fails | the optional card is silently omitted; the main statistics keep their own error state | left |
| Plugin popup UI and logs | request fails | intentionally tolerated; the popup shows the remaining sections | left |

## Exercised with injected failures

Run against the development stack with the browser's `fetch` forced to answer 500 for one endpoint at a time, then cleared to check that Retry recovers.

| Flow | Injected failure | Result |
| --- | --- | --- |
| Game library, returning to `/games` with a library already seen | `/api/game/list` answers 500 | **fixed**: the cached games disappeared and the error had no Retry. The list now stays, with an alert "The list below may be out of date" and **Retry** (`GameLibrary.vue`) |
| Game page favourite heart | `PATCH /api/game/update/:id` answers 500 | **fixed**: the heart reverted with no message and a second click could race the first. It now shows "Could not update the favourite" beside the heart and ignores clicks while saving (`useFavoriteToggle`) |
| Library favourite star | same | **fixed**: same silent revert; now names the game and ignores a second click while saving |
| Game page | game requests answer 500 | already covered: "We couldn't load this game" with **Try again** and a way back |
| Media lists index | `/api/lists` answers 500 | covered by the earlier fix; Retry restores the lists |
| Settings > Users | `/api/auth/users` answers 500 | covered by the earlier fix; message with Retry |
| Collections index and detail | games request answers 500 | covered by the earlier fix (screenshots above) |

Login, accepting an invitation, first-run setup and the upload section were read, not exercised: each guards its submit with a busy or saving flag, clears it in `finally` and shows the error.

## Not audited yet

Notification pages (they follow the notification overhaul contract), the rest of the game page (`useGameDetail` has 37 error handlers; only load and favourite were exercised), anime, movie and TV detail views, the Steam and connection dialogs and the plugin manager's remaining handlers. The anime, movie, TV and plugin flows need provider data or a running plugin runtime, which the test stack did not have. Each needs the same four questions asked against its real requests.

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

### Phone width and Classic

The collection pages were also checked at 375 px in Modern and Classic with the same forced failure. Both keep the layout (header, actions, error and **Retry** on their own lines, no horizontal scroll).

| Collection detail | Modern, phone | Classic, phone |
| --- | --- | --- |
| After | ![Modern theme at phone width, failure with Retry](../assets/ui-state-audit/collection-detail-modern-phone.jpg) | ![Classic theme at phone width, failure with Retry](../assets/ui-state-audit/collection-detail-classic-phone.jpg) |

Achievement notes are covered by tests only (the test stack has no achievements). The collection detail page also shows two messages for one failure (the raw "Failed to fetch games: 500" and the friendlier "Could not load this collection"), which is redundant but harmless.

### Setup, invitations and Steam settings

| Flow | After |
| --- | --- |
| Setup, configuration could not be loaded | ![Setup failure with Try again](../assets/ui-state-audit/setup-load-failure-after.png) |
| Accept invitation, availability check failed | ![Invitation check failure with Try again](../assets/ui-state-audit/invite-check-failure-after.png) |
| Steam wishlist, preference could not be loaded | ![Locked switch with Retry](../assets/ui-state-audit/steam-wishlist-load-failure-after.png) |

### More screenshots

| Flow | Screenshot |
| --- | --- |
| Settings > Users, request failed | ![Users failure with Retry](../assets/ui-state-audit/settings-users-failure.jpg) |
| Media lists, request failed | ![Media lists failure with Retry](../assets/ui-state-audit/media-lists-failure.jpg) |
| Library refresh failed, games kept | ![Library with the old list and a Retry alert](../assets/ui-state-audit/library-refresh-failure-after.jpg) |
| Favourite change failed on the game page | ![Message beside the heart](../assets/ui-state-audit/favourite-toggle-failure.jpg) |
