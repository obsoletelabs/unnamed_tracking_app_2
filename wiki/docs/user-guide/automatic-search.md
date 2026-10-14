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
