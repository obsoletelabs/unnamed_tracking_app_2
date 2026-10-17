# AniList Import

Unnamed Tracking App can import anime from a user's public AniList list into that user's anime library.

## What the import does

The AniList import reads your public anime list from AniList and creates or updates matching anime in your Unnamed Tracking App library.

The integration is **read-only with respect to AniList**. Unnamed Tracking App does not write changes back to your AniList account.

Matching uses the AniList title identifier, so an existing library entry with the same AniList ID can be updated instead of creating a duplicate.

## Configure automatic imports

Open **Settings → Export/Import → AniList Import**.

The settings are stored per user, so each account can configure its own AniList username and schedule.

### Automatic import

Enable **Automatic import** to have the application periodically import your public AniList list.

Automatic import is disabled by default.

### AniList username

Enter the AniList username whose public anime list should be imported.

The username is limited to 100 characters and is trimmed before it is saved.

The list must be publicly accessible to the application.

### Import frequency

The built-in frequency choices are:

- **Hourly**
- **Every 6 hours**
- **Twice daily**
- **Daily**
- **Weekly**

You can instead enter a custom interval in hours. Custom intervals may be from **1 to 720 hours** (1 hour to 30 days).

The backend stores the interval in minutes.

### Update existing titles

Enable **Update existing titles** if changes from AniList should also be applied to anime already in your library.

When this option is disabled, existing matching titles are skipped while new titles are imported.

An import can report fetched, created, updated, and skipped entries, along with a limited list of per-entry errors.

## What gets imported

For imported anime, the AniList data used by the application can include:

- title and sort title;
- description;
- first-air, start, and end dates;
- episode count and watched progress;
- episode runtime;
- studios, countries, genres, and format;
- AniList score and AniList ID;
- poster and backdrop artwork;
- status and priority;
- rewatches and notes;
- overall rating.

Imported entries are created with a season record containing the AniList episode count, current progress, and status.

## Scheduling behavior

The schedule is the personal task **AniList import** in **Settings → Account → Background tasks**, driven by the application's existing background scheduler rather than a separate worker. Turn it on there, choose how often it runs (1 hour to 30 days), run it now and see the last result. The username and the update-existing choice stay in the AniList Import settings.

The scheduler wakes approximately once per minute and starts every enabled task whose interval has elapsed since its last run, oldest run first. At most **4 imports run at once** so that a large multi-user deployment cannot monopolize the application scheduler; tasks that are not due yet never take a slot from tasks that are. Disabled tasks, inactive accounts and accounts without an AniList username are never imported.

Each user has an independent task: enabled state, interval, last run and last result belong to that account only, and a user's schedule does not change another user's schedule.

Every run is recorded, whether it succeeds or fails. A failed import shows a short, safe reason as the task's last result and is tried again when the interval next elapses, not every minute. A later successful import replaces the reason with its counts.

## Defaults

New users receive these AniList import defaults:

| Setting | Default |
| --- | --- |
| AniList import task | Off |
| AniList username | Empty |
| Task interval | Daily (24 hours) |
| Update existing titles | Disabled |

## Manual versus automatic import

The scheduled path uses the reusable AniList import service rather than maintaining a separate import implementation. The scheduler is responsible for deciding when an enabled user's import is due.

For troubleshooting an import, first check the AniList username, confirm that the list is public, and verify that the AniList import task is on if you expect it to run on a schedule. A failed run shows its reason as the task's last result in Background tasks; a manual import from the anime library does not change the schedule.
