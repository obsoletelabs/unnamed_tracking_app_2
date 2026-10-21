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
