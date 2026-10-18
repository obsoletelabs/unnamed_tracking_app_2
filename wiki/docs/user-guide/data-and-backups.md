# Data, Exports and Backups

Unnamed Tracking App stores library records in PostgreSQL and larger user files on the application's persistent data directory.

## Export / Import settings

**Settings → Export / Import** groups every library data action into four tabs. Each tab keeps its own progress, errors and results, so a preview or error in one workflow never appears under another.

| Tab | Actions | Formats |
| --- | --- | --- |
| **Export** | Export library, Export CSV | JSON library export (`library-export-YYYY-MM-DD.json`); CSV of movies, TV shows and anime (`media-library-YYYY-MM-DD.csv`) |
| **Import** | Import from another site, AniList import | MyAnimeList `.xml`/`.xml.gz`; Letterboxd `.zip`/`.csv`; IMDb `.csv`; Yamtrack `.csv`; AniList by username |
| **Restore** | Restore movies, TV shows and anime; Restore games | JSON library export or backup file |
| **Backups** | Automatic backup status | Server-side snapshots, not downloadable |

Settings search finds each tab directly, for example "letterboxd", "spreadsheet", "restore from export" or "disaster recovery".

The CSV export is for spreadsheets only and cannot be restored. Use the JSON export to move a library between servers.

## Automatic backups

The application runs an in-process automatic backup loop. It runs once at startup and then every 24 hours.

Automatic backups are per-user JSON snapshots containing games, movies, TV shows and anime.

Up to **7** automatic backups are retained for each user:

```text
/data/backups/<user-id>/
```

These are a rolling safety net, not a complete deployment backup. The **Backups** tab shows whether they are enabled, how often they run, and when the last one was written.

## Library export

The library export is a user-scoped JSON snapshot containing games, movies, TV shows and anime, including manual achievements and completion choices:

```http
GET /api/export/library
```

A library export is a portable copy of one user's library. It is not a disaster-recovery backup: it does not contain attached files and does not cover other users. See [Moving a deployment](#moving-a-deployment).

## Importing from other sites

Imports from MyAnimeList, Letterboxd and IMDb show a preview first: how many titles are new, already present with differences, or identical. For titles that differ you choose, title by title, whether to keep the existing data or use the file's data. Nothing changes until you confirm. The result reports titles added, updated and kept, plus details that could not be filled in.

Yamtrack imports directly without a preview and skips items already in the library. Anime from Yamtrack is not imported. The result reports added and skipped counts and any rows that failed.

## Restoring from a library export

The **Restore** tab has two actions that read the same JSON library export.

**Restore movies, TV shows and anime** recreates movies, TV shows and anime with their seasons and watched episodes. Titles already in the library (same title and year) are skipped, never overwritten. The result lists added and skipped counts per kind and any entries that failed.

**Restore games** recreates game records with their identity, manual achievements and completion choices. It does **not** restore:

- screenshots or other folder assets
- save archives

Existing games are never overwritten. Game folder-name collisions are given a new available folder name rather than overwriting an existing game. The result lists imported and skipped counts, restored manual achievements and completion choices, and any entries that failed.

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
