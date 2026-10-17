# Game Media and Files

A game's page keeps your screenshots, clips, soundtrack, saves, docs and
Minecraft worlds. They all work the same way: one grid of cards, one add card,
and the same tools above it. Only what is specific to the kind of file
differs.

| Tab | What it holds |
| --- | --- |
| Screenshots | Images, shown full size in a viewer. |
| Clips | Videos, with a saved preview picture and the real length on each card. |
| Soundtrack | Audio. A track plays right on its card. |
| Saves | Named save files, each with a history of versions. |
| Docs | Manuals, guides and any other file. |
| Worlds | Minecraft worlds, shown on Minecraft games. Zip the world folder (the one with `level.dat`) and add it. |

## Adding files

There are three ways, and they all do the same thing:

- Click the big **add card** at the start of the grid.
- Drop files anywhere on the page. A drop box appears over the grid, not in the
  middle of the screen.
- Paste an image with Ctrl+V (screenshots).

Several files can be added at once. Saves and worlds ask you to name them
first, since one game can hold any number of named saves.

Media uploads use the filename extension and inspect the content, rather than
trusting the type reported by the browser. Supported images include PNG, JPEG/JFIF,
GIF, WebP, AVIF, BMP, TIFF, HEIC/HEIF and ICO. Clips accept MP4/M4V, WebM, MOV, MKV,
AVI and OGV; soundtrack accepts MP3, Ogg/OGA/Opus, WAV, FLAC, M4A and AAC. Playback
uses the codecs supported by your browser. HTML and SVG belong in **Docs**, where
they download as attachments, rather than in the media gallery.

Invalid or oversized files are rejected individually; valid files in the same
batch still save. Images use the general upload cap and a 40-million-pixel limit.
Clips and soundtrack use the separate clip cap set in **Administration → Limits**.

## Docs

**Docs** takes manuals, guides and any other file: Word documents (`.docx` and
`.doc`), PDFs, spreadsheets, presentations, text, archives, programs or files with
no extension at all. The tab does not check a file's type or contents, so no file
is refused for its format. Each file has the **Documents** cap in
**Administration → Limits** (`MAX_DOC_SIZE_MB`, 500 MB by default), which is
separate from the images/general cap. A file over it is rejected with
"Larger than N MB." and the rest of the batch still saves. Modpacks in **Worlds**
follow the world-save cap.

Docs are stored byte for byte under a sanitised name in the game's own folder, and
only their owner can list or download them. A download is always an attachment
(`application/octet-stream` with `X-Content-Type-Options: nosniff` and a sandboxing
Content-Security-Policy) under the preserved filename. An uploaded HTML or SVG file
is therefore saved to disk, never opened as a page of the app.

Reading a doc in the app is separate from storing it. When a document-reader
plugin is enabled, a doc's name opens that plugin's reading preview in a new tab.
Through the Plugin API a reader only receives previews of PDFs, UTF-8 text, HTML
(as text) and inactive `.docx`, `.pptx`, `.odt` and `.odp` files within the
preview limits. Other files, such as legacy `.doc`, spreadsheets, macro-enabled
Office files and documents with embedded objects, are not handed to the reader as
a preview, and the reader explains that instead. That never affects the upload:
the file stays in Docs and its **Download** button still works.

If an upload fails with a message about "a proxy in front of the app", a reverse
proxy placed in front of the container refused the request before it reached the
app. Raise that proxy's request body limit; see
[Upload transport](../deployment/production-docker.md#upload-transport).

New uploads preserve their Unicode filename separately from the safe name used
on disk. That name follows a file from Upload into a game and through delete/restore.
Changing its title only changes its label inside the app. Older files retain their
existing names; characters lost before this change cannot be reconstructed.

## Finding things

- **Search** matches the title, the file name, your note, the tags and the
  name of the achievement a file is tied to.
- **Sort** by Newest first, Oldest first or Name.
- **Filter** to everything, or only the files tied to an achievement.

## Selecting several

**Select** turns every card into a checkbox, and **Select all** picks
everything the search is showing. With some chosen you can:

- **Set date** on all of them at once;
- **Detect dates** to work each one out again from its file;
- **Use achievement dates** for files tied to an unlocked achievement;
- tie them all to one achievement;
- delete them together.

## Editing a file

The edit button on a card (or clicking the card of a save or doc) opens one
dialog with everything about that file:

| Field | Notes |
| --- | --- |
| Title | A name that is only used in the app. The file keeps its own name, which stays visible under the title. |
| Note | Free text. |
| Tags | Type a tag and press Enter or a comma. |
| Date | When it was really taken. See below. |
| Achievement | Ties the file to one of the game's achievements. |
| Account | For games with accounts (for example an OSRS profile), which account it belongs to. |

### Dates

A file's own modified date is often wrong, because copying or syncing gives
every file the day you moved it. So the app works out when a screenshot or
clip was taken from the most trustworthy place available, in this order:

1. the file's own data (photo EXIF or PNG creation time, or a video's creation
   time);
2. the file name (Steam, PlayStation, Xbox, NVIDIA and most capture tools put
   the time in it);
3. the file's modified date as your browser reports it;
4. the moment you uploaded it.

The dialog says where the date came from, so you can tell a fact from a guess.
**Detect from file** works the date out again, and you can always type your
own. When you tie a file to an achievement, you can choose to take the
achievement's unlock date as the file's date (**Tying also sets the date**).

## Copying and downloading

Every card can copy a link to its file or download it. Saves download their
latest version, and the edit dialog lists every version so you can download an
older one, add a new one, or delete one you no longer want. Worlds keep the
same named archives, notes, tags and version downloads.

Media and document downloads use the preserved filename when available. Client
paths and control characters are removed; display/download names are limited to
500 characters, and stored names to 240 ASCII bytes. A long stored name keeps its
extension, and date detection still uses the preserved name.

BlueMap rendering and its map viewer have been retired. Existing uploaded
worlds and versions remain available in **Worlds**; the old `World Map` tab
links open that tab. Legacy render files are left on disk, including through
archive Trash/restore, but are no longer served. The former
`/api/game/{game_id}/world-map/…` rendering, status, thumbnail and viewer
endpoints return 404. No Java runtime or BlueMap download is required.

## Deleting

Deleting moves a file to the trash. **Deleted (N)** at the top of the tab shows
what is in it, and a file can be restored from there. Files stay in the trash
for 7 days and are then removed for good.
