# Data, Exports and Backups

Unnamed Tracking App stores library records in PostgreSQL and larger user files on the application's persistent data directory.

## Where things are in Settings

**Settings → Export / Import** has four tabs, one per workflow, so a long page never mixes them. Each tab keeps its own
preview, progress and error messages when you switch between them, so a half-reviewed import is not lost.

| Tab | What it is for |
| --- | --- |
| **Export** | download your library as one JSON file, or movies, TV shows and anime as a CSV spreadsheet |
| **Import** | bring in a list from another site (MyAnimeList, Letterboxd, IMDb, Yamtrack) with a preview and per-title choices, and the AniList username import |
| **Restore** | add movies, TV shows and anime back from a library export or backup file, and import games from an exported file; existing items are skipped, never overwritten |
| **Backups** | the status of the automatic backups, and an explanation of how they differ from a full backup |

A **library export is not a disaster-recovery backup.** It holds titles, progress and metadata; it does not hold attached
files (screenshots, saves, documents), accounts, settings or credentials. Only a copy of the database and the data volume
taken by whoever runs the server restores everything. Every earlier action and file format is still available, and
`/settings?section=export&tab=restore` (or `import`, `backups`) opens a tab directly.

## Automatic backups

The application runs an in-process automatic backup loop. It runs once at startup and then every 24 hours.

Automatic backups are per-user JSON snapshots containing games, movies, TV shows and anime.

Up to **7** automatic backups are retained for each user:

```text
/data/backups/<user-id>/
```

These are a rolling safety net, not a complete deployment backup.

Server operators can use [Full Deployment Backups](../deployment/backups.md) to schedule encrypted
PostgreSQL, application-file and plugin-state backups with a maintenance window and an isolated
restore drill. This operator workflow does not change the Settings import/export tabs.

## Library export

The library export is a user-scoped JSON snapshot containing games, movies, TV shows and anime:

```http
GET /api/export/library
```

## Library import

The generic importer is currently more limited than export. It accepts game records and recreates them for the authenticated user.

It does **not** generically restore:

- movies
- TV shows
- anime
- screenshots or other folder assets
- save archives

Game folder-name collisions are given a new available folder name rather than overwriting an existing game.

## Save files and game assets

Game assets and save archives are stored on disk under the owning user's directory. They are not part of the JSON library export.

A game's persistent directory is:

```text
/data/users/<user-id>/games/<folder-location>/
```

Game assets use files such as `key_art.png`, `banner.png`, `logo.png`, and `icon.png`. Notes, screenshots and clips have their own directories. Clip thumbnails are kept beside the clips.

Notes are plain `.md` files. Their extra details (created date, pin, tags, the achievement they are tied to) and their saved versions are stored in PostgreSQL, so a note's history is part of the database, not the folder.


### Game note titles

Game notes use a human-readable title rather than requiring a filesystem-style name. Titles may contain spaces and common punctuation, for example "what is this for", "Quest Log - Main Story", or "Build #2 (final!)".

Titles must not contain path separators, control characters, drive-style paths, or other path-like values. Empty titles, ".", "..", hidden/path-like names, and Windows-reserved device names are rejected. A trailing ".md" is treated as the note's display title without the extension for compatibility.

Creating a note whose title already exists does **not** replace the existing note. The API returns a conflict and the Notes editor keeps the operation in an editable state so the user can choose another title or cancel.

Editing an existing note saves its current title normally. Renaming uses a dedicated rename operation: the destination must be unused, the existing content is preserved, and renaming to the same title is a no-op. A failed rename leaves the original note in place.

The Notes UI translates validation, conflict, loading, saving, deletion, and rename failures into user-facing messages rather than displaying raw backend status or JSON responses.

Named save archives are stored under the game directory in `saves/` or `world_saves/`.

## Game trash

Deleted screenshots, clips, soundtracks, documents, modpacks, save archives and archive versions move to a `_trash/` folder inside their game's directory:

```text
/data/users/<user-id>/games/<folder-location>/_trash/
```

A deleted game's whole directory, including its own `_trash/`, moves to:

```text
/data/users/_trash/<game-id>/
```

Trashed items can be restored for **7** days. A background sweep runs at startup and then every six hours, and permanently removes anything older than that. Back up these folders together with the rest of `/data` if you want to keep items that are still restorable.

The sweep skips an item and logs `Trash sweep kept … for a later pass` when its storage is not safe to use, for example when a game's folder rename was interrupted or a directory on its path is a symbolic link. Nothing is removed for a skipped item. Retrying the game's folder-name save reconciles an interrupted rename, and the next sweep then removes the item. Do not delete the `.folder-moves/` recovery records by hand.

## The image cache

Smaller copies of artwork are kept in a cache folder for each user:

```text
/data/users/<user-id>/.cache/
```

`asset-previews/` holds resized copies of a game's banner and cover, and `media-images/` holds local copies of movie, TV and anime posters and backdrops. Everything in it can be rebuilt from the originals (or downloaded again), so it does not need to be backed up, and deleting it is safe. It is rebuilt as pictures are needed.

## Moving a deployment

A complete deployment therefore needs both:

1. PostgreSQL data; and
2. the persistent application data directory.

A JSON library export does not reproduce all stored files, and copying only `/data` does not reproduce the PostgreSQL database.
