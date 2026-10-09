# Movie, TV and Anime Libraries

Status tabs survive a page refresh. Loading the next page keeps the existing cards
on screen and preserves their scroll position. Posters, backdrops and achievement
icons are downloaded into the server's cache when saved, with the original URL used
as a fallback if the download fails. Existing images are cached when first viewed.

After a connection loss, the visible library retries when internet access returns.
If the tab was hidden, it waits until you return. A failed load also retries when
you focus the window again. Successful requests clear the previous error and keep
your selected filters; you do not need to reload the browser page.

Yamtrack CSV imports continue to skip anime entries while their mapping and duplicate
matching remain unresolved. Movies and TV shows can still be imported.

These three libraries share one layout, so what is described here applies to
all of them. Their own pages cover what is specific to each.

## Rank

A title with a score gets a **rank**: its place among every title you have
scored in that library, highest score first. Titles with equal scores are
ordered by name, so each rank is different. Ranks are worked out over the whole
library, so they stay the same whether you are searching, scrolling or looking
at only one page of it.

Rank shows as a badge on cards, in the Rank column of the list layout, and as
the **Sort: Rank** order. Movies, TV shows and anime are ranked separately.

## Filtering from a title's page

The genres on a title's page are links. Clicking one opens that library with
only that genre selected and the filter panel open, replacing whatever filters
were left on before. This works on Movie, TV and Anime pages.

## Pictures

Posters and backdrops are stored by TMDB or AniList, but your server keeps its
own small copy of each one. The first time a picture is needed, the server
downloads it, shrinks it (posters to 400 pixels wide, backdrops to 1920) and
stores it. After that, pages load it from your own server and nothing waits on
another site.

- Changing a title's poster or backdrop address makes a fresh copy.
- If a download fails, your browser is sent to the original address instead, so
  a picture never just disappears.
- The server only fetches public web addresses. Addresses that point at your own
  machine or local network are refused.
- The copies are a cache and can be deleted at any time. They are made again when
  needed. See [Data, Exports and Backups](data-and-backups.md).

Libraries also load pictures lazily: only the cards near the screen fetch
theirs, and more load as you scroll.

## Editing

Movies, TV shows and anime use the same Add and Edit dialog layout. The search
box at the top fills in the form from a metadata provider, and fields you have
changed yourself are kept rather than overwritten (the dialog tells you which
it skipped).

## Metadata provider extension (1.1.1)

Search and refresh use the hardcoded core providers and optional installed provider plugins
through one shared metadata handler. Core search does not require the plugin runtime.
Provider configuration and health appear in the host's metadata settings.
See [the progressive metadata contract](../development/metadata-providers.md) for
phase separation, scoped credential migration, deadlines and persistence behavior.
