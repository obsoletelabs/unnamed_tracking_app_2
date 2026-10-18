# Automatic metadata search

**Automatic Search** controls requests to metadata providers while typing a new
game/media title or a metadata query. Open **Preferences → Metadata** and choose
**My account** to change the account master switch and individual providers.
With the master switch off, no provider runs automatically. Per-provider choices
apply while it is on. Settings remain on the account across devices.

Search/Enter still performs an explicit search. Selecting a result, requesting
artwork, and scheduled library/metadata refreshes continue to work. Filtering
your library and filtering already fetched candidates remain local and do not
depend on Automatic Search. The form explains how to search explicitly when
typing-triggered fetching is off.

**Use for metadata** is a separate account choice on each provider tile. Turning
it off also blocks explicit searches, enrichment, artwork and scheduled metadata
work for that provider, including when it uses server credentials. Saved Automatic
Search choices and credentials remain intact. Connected-library ownership,
playtime and achievement sync are unaffected. See
[provider selection](../integrations/metadata.md#provider-selection-and-priority).

Administrators use the same provider tiles with **System default** selected.
They can restrict Automatic Search server-wide or for any built-in or installed
metadata provider, including providers that do not use credentials.

| Situation | Automatic Search |
| --- | --- |
| Account master off | Disabled for every provider |
| Account provider choice off | Disabled for that provider |
| Server master/provider off and server credentials used | Disabled |
| Server restriction on a credential-free provider | Disabled |
| All credentials used by a provider are personal | Account choices apply; server restriction may be overridden |
| Personal and server credentials used together | Server restriction still applies |

Personal credentials do not grant plugin permissions or bypass installation,
authorization, health and provider capability checks. Explicit searches use the
normal provider configuration even when Automatic Search is restricted.

## When no provider can answer

The game metadata search in the game editor and the Add media search show a note
beside the search box when no provider can answer: none is installed for the media
type, the account turned the providers off, or the remaining providers are
disabled, lack credentials or have lost a plugin permission. The note names the
provider and the missing setup (never a credential value) and links to the place
that fixes it; the Search button is disabled and refers to the note. The list
refreshes when a provider is configured, enabled, disabled or removed, and when
the window regains focus, and text you have typed is kept. The server checks again
when a search starts and refuses it with the same explanation if setup changed
after the page rendered.
