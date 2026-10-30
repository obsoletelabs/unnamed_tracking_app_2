# Input boundary audit

This records the audit of username handling and untrusted input behind issue #293. It describes what was checked, what was changed and what remains. Regression tests are in `src/backend/tests/test_input_boundaries.py` and `src/backend/tests/test_username_validation.py`.

## Usernames

| Path | Validation | Uniqueness |
| --- | --- | --- |
| First-run setup | `validate_username` | First account; the unique constraint applies |
| Environment bootstrap (`PRIMARY_USER_*`) | `validate_username` for a new account; an existing account is matched exactly | Exact |
| Invitation acceptance | `validate_username` | **Now case-insensitive** (was exact) |
| Profile rename | `validate_username` | **Now case-insensitive** (was exact); a collision follows the existing disclosure rule: members get the combined 409 message, so the reply cannot be used to probe names, and administrators are told the username is taken |
| OIDC provisioning | Characters reduced to ASCII letters, digits, `.`, `_`, `-`, then `validate_username` | Case-insensitive through `username_in_use`, numeric suffix on collision |
| Legacy accounts | Not re-validated and never renamed; sign-in still matches the stored name exactly, so existing pairs such as `Admin` and `admin` keep working | |

The accepted alphabet stays `[A-Za-z0-9_.-]`, at most 100 characters. That rules out whitespace, control and bidirectional characters, `@`, path separators, quotes, markup and non-ASCII look-alikes for new and renamed accounts. Words such as `NULL` and `DROP` are ordinary strings and remain valid.

**Compatibility strategy.** No migration and no change to sign-in: only account creation and rename refuse a name that differs from an existing one only by ASCII case. A case-insensitive unique index is deliberately not added because legacy databases may already contain such pairs and an index would fail the upgrade.

Reserved names (`admin`, `root`, ...) are not blocked. The administrator account is determined by the `is_admin` flag, never by its name, so a reserved-name list would only affect presentation; it is left as a product decision.

## Untrusted fields

| Area | Result |
| --- | --- |
| SQL | All queries use bound parameters or SQLAlchemy expressions. The only raw SQL is constant text (advisory locks, probes) and one migration helper that interpolates fixed table names. No request value selects a column or sort direction: library sorting uses the `LibrarySort` literal. |
| LIKE patterns | **Fixed.** The game list search (`/api/game/list`) put `%` and `_` from the query into the pattern, so `%` matched every game and `_` any character. It now uses the same escaping helper as the movie, TV and anime searches. Administrator and plugin session filters used `ILIKE` with the country as a pattern; both now compare lower-cased values exactly. |
| HTML rendering | Game descriptions, notes (Markdown) and plugin READMEs are sanitised with DOMPurify before `v-html`; rich notification content uses fixed elements and never `v-html`. Other `v-html` uses render fixed icon strings. No unsanitised path was found. |
| Redirects | OIDC and Steam sign-in redirects target fixed application paths. **Fixed:** the image fallback (`original_image_response`) redirected the browser to the stored poster or icon address when the host could not fetch it. Those addresses come from providers, imports or plugins, so only plain `http(s)` addresses without credentials, backslashes or control characters are now redirected to; others answer 404. |
| Path traversal | Game folders must match a strict name pattern and are resolved beneath the owner with symlink checks (`game_folders.owned_folder`); stored file names are reduced with `Path(name).name` before use and uploads get a random prefix and a restricted character set. Tests post hostile folder names and request traversal file names. |
| SSRF | Calendar, remote image and delegated fetching use `helpers/public_http.py`, which resolves once, rejects non-public addresses and pins the connection. Plugin outbound calls use the runtime's egress policy. See "Left as is" for the plugin package download. |
| Command injection | No request value reaches a shell or subprocess in the host. |
| Error output | Validation errors drop submitted values (`main.py`), and logs redact notification links. |

## Left as is (decisions or follow-ups)

* **Plugin package download (`plugin_manager/acquisition.py`)** validates the resolved addresses and every redirect, but then lets `httpx` resolve the name again, so a DNS rebinding answer between the two lookups is theoretically possible. It is administrator-only and the downloaded package is still signature-verified before installation. Moving it onto the pinned `public_http` connection is a reasonable follow-up.
* **Steam OpenID origin.** Without a configured `PUBLIC_APP_URL` the return address is derived from the `Origin` header of the authenticated request. The value is normalised, and the callback recomputes and verifies the same address, but setting `PUBLIC_APP_URL` is recommended.
* **External images in sanitised HTML.** DOMPurify keeps `https` images from provider descriptions, which lets a provider observe viewers' addresses. Proxying them would change how descriptions render and is a product decision.

## Plugin-supplied responses and UI content (#333)

The #293 audit covered `v-html` and host-side field handling. This pass followed hostile plugin output across the runtime boundary for each plugin-fed surface: files and pages the host serves for a plugin, metadata provider results, notification content, and plugin UI documents and messages. It was done by reading each path end to end and then adding negative tests for what was confirmed; no live plugin was run.

### Fixed

| Surface | Finding | Fix and test |
| --- | --- | --- |
| Plugin page files, `/api/plugins/{id}/frontend/...` (also the "runs unsandboxed when opened directly" item in #383) | The response CSP had no `sandbox`, so the page was isolated only because the host embeds it in a sandboxed iframe. Opened directly (a link, a notification action) it ran the plugin's script on the host origin with the viewer's session. | The CSP now starts with `sandbox allow-scripts` (the same flags as the iframe) and adds `form-action 'none'`. `test_plugin_documents.py` |
| Plugin page files and public-app files | A double-encoded path (`frontend/%252e%252e/ui.json`) passed the host's `..` check, then the runtime decoded it a second time to `frontend/../ui.json`, which stayed inside the package and returned the plugin's own source and manifest (disclosure of the plugin's package only). | The host rejects a path that decodes to a dot segment and quotes the path it forwards; the runtime now requires the file to resolve inside the served root (`frontend/` or `native/`), not merely inside the package. `test_plugin_documents.py`, `plugin-runtime/tests/test_runtime.py` |
| Image fallback (`original_image_response`) | When the host refused to fetch an image because its address is not public, the fallback redirected the **browser** to it, so a plugin-supplied `poster_url`, `backdrop_url` or icon could aim the viewer's browser at their LAN (`http://192.168.1.1/...`, `http://169.254.169.254/...`). | The fallback now returns 404 for a private IP literal, `localhost`, a single-label name or a local-looking suffix (`.local`, `.lan`, `.internal`, ...). `test_input_boundaries.py` |
| Media sync `poster_url` | Unlike `backdrop_url` and artwork, it had no scheme or host validation and was stored and sent raw to the frontend. | It must be an HTTP(S) address without credentials. A private host stays allowed because media servers such as Jellyfin serve posters from the user's own network; the image fallback above still refuses to redirect the browser there. `test_plugin_media_sync.py` |
| Plugin UI frame messages | A sandboxed plugin frame could post `plugin.run-action` at any time; for an action declared `external_navigation` the host fell back to `window.location.assign`, sending the whole app to a destination the plugin chose, with no click. | The host now refuses an external-navigation action unless the page has transient user activation. `pluginExternalNavigation.test.ts` |
| Plugin library sync and enrichment, `provider_ids` | `{**existing, **plugin}` let a sync or enrichment response overwrite a saved id, ignoring the `provider_ids` lock and the duplicate-link check the manual refresh applies. | Saved ids win (`merge_provider_ids`), the lock is honoured, and `_enrich_new_game` skips ids another game already holds (`unclaimed_provider_ids`). `test_plugin_metadata_integrity.py` |
| Metadata provider results, identifier namespaces | `identifier_namespace` only routed requests; a plugin could return `steam` or `igdb` ids and join another provider's result through `same_entity`. | `PluginMetadataProvider.invoke` drops ids outside the provider's own id and declared namespace from candidates, relations and patches (`confine_to_namespaces`). `test_plugin_metadata_integrity.py` |
| Metadata contract limits | Limits wider than the columns (`format` 64 vs 20, `age_rating` 64 vs 20, title 512 vs 500, link label 128 vs 50, link URL unbounded vs 2048, time to beat above `Numeric(6,1)`) made one response fail a refresh, and the anime heal sweep had no per-row rollback. | The host clips these values to the column sizes before saving (the public contract is unchanged) and each heal row runs in a savepoint. `test_plugin_metadata_integrity.py` |
| Game metadata refresh `links` and `release_date` | Provider `links` were assigned as dicts to a relationship that expects `GameLink` rows, and `release_date` as an ISO string. | Both are converted (links clipped to the column sizes). `test_plugin_metadata_integrity.py` |
| Notification text | Bidirectional overrides and zero-width characters were accepted in titles, labels and facts (`Invoice ‮gpj.exe` displays as `Invoice exe.jpg`), and the typed-template path (`emit_source`) skipped the content checks of the canonical path. | Canonical content and template output are rejected when they contain U+200B, U+2060, U+202A-202E, U+2066-2069 or U+FEFF; the legacy title and body path strips them. The direction marks U+200E/U+200F and joiners U+200C/U+200D stay, since right-to-left text and emoji need them. `test_notification_content_spoofing.py`, `test_notification_sources.py` |

### Found, not fixed here (tracked in the follow-up issue)

* **Game metadata refresh `links`.** The refresh route passes plugin `links` as dictionaries to a relationship that expects `GameLink` rows.
* **Episode stills** (`still_url`) are stored as any HTTP(S) address and loaded raw by the browser; other plugin image URLs also reach the browser directly rather than through the image proxy.
* **Notification contract.** ~~Most plugin UI document collections have no count limit.~~ Fixed: `PluginUiDocument` rejects more than 256 entries in any contribution collection and more than 64 columns in a table (checked in a validator, so the published schema is unchanged).
* **Install consent preview** loads a manifest-chosen icon URL (including plain `http`) in the administrator's browser before consent.
* **Public-app files** (`/api/public-apps/{id}/app/...`) are served unauthenticated with `connect-src 'self'` and no sandbox, because the app talks to the host. Sandboxing them needs a separate origin or a design change for the Discord embed; this is a decision, not a quick fix.
* **Runtime output and body limits.** A single plugin route result line is read without a cap in the runtime (only the memory rlimit bounds it; left for the runtime owner). ~~A chunked request body is buffered before the 48 KiB check.~~ Fixed: the route reads the body as a stream and stops at the limit.
* **Smaller caps.** Fixed: two achievement ids that share their first 200 characters are one row (the stored, clipped id is what is de-duplicated), and provider-reported `provider_custom` fields stop growing at 64 keys.
* A notification link to the app's own `/api/plugins/.../frontend/...` is accepted as an external HTTPS link. With the CSP sandbox above it is no longer dangerous, but it should be rejected as defence in depth.

### Checked and found safe

Plugin response headers never reach the browser (the host builds the response from a status code and a JSON body); plugin routes require the user's session and grants, with `Cache-Control: private, no-store`; plugin documents download as attachments and HTML documents are returned inside JSON; PWA assets are allow-listed, size-capped and re-encoded; host-side artwork fetching goes through `public_http`; candidate ownership and ids are host-assigned; the notification renderers escape and rebuild rather than pass HTML through, email and webhook delivery cannot inject headers or mentions, and plugin provider errors become fixed codes; the plugin iframe is `sandbox="allow-scripts"` with an origin and source check on messages.
