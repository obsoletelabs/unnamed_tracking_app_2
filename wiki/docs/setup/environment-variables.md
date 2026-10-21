# Environment Variables

The backend configuration registry is the source of truth for application configuration exposed through environment variables. Environment values take precedence over values stored by setup.

The application also reads a small number of deployment/runtime variables directly, and the Docker Compose deployment has its own interpolation variables. They are listed separately below.

A variable set to an empty value (`SMTP_PORT=` as in `example.env`) is treated as unset and uses
its default.

## Database

The application accepts either the individual PostgreSQL variables or the legacy `DATABASE_URL` alternative.

| Variable | Source | Default / requirement | Purpose |
|---|---|---|---|
| `POSTGRES_USER` | ENV | Required with `POSTGRES_PASSWORD` and `POSTGRES_DB` | PostgreSQL username. |
| `POSTGRES_PASSWORD` | ENV | Required with `POSTGRES_USER` and `POSTGRES_DB` | PostgreSQL password. |
| `POSTGRES_DB` | ENV | Required with `POSTGRES_USER` and `POSTGRES_DB` | PostgreSQL database name. |
| `POSTGRES_HOST` | ENV | `db` | PostgreSQL host. |
| `POSTGRES_PORT` | ENV | `5432` | PostgreSQL port. |
| `DATABASE_URL` | ENV | Alternative; deprecated | Legacy PostgreSQL connection URL; redacted from configuration schema. If the three component variables are complete, they take precedence. |

`DATABASE_URL` is still supported for compatibility. New deployments should use the individual PostgreSQL variables.

## Application

Environment-only authentication quota controls (`AUTH_RATE_LIMIT_PERCENT`, `AUTH_LOGIN_ACCOUNT_PER_MINUTE`, `AUTH_LOGIN_ACCOUNT_PER_15_MINUTES`, `AUTH_LOGIN_IP_PER_MINUTE`, `AUTH_LOGIN_IP_FAILURES_PER_15_MINUTES` and `AUTH_PASSWORD_WORKERS`) are documented with defaults, accepted ranges and replica/proxy requirements in [Authentication Protection](../deployment/authentication-protection.md).

| Variable | Source | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | ENV | Generated/persisted when omitted | Stable Fernet encryption and session-signing key. It is hidden from the setup UI. |
| `AUTH_COOKIE_SECURE` | BOTH | `false` | Makes authentication cookies require HTTPS when enabled. |
| `DEBUG` | BOTH | `false` | Enables backend debug mode. |
| `STARTUP_MODE` | ENV | Empty/default | Selects startup defaults and whether the setup UI is shown. |
| `DEFAULT_UI_STYLE` | BOTH | `modern` | Default built-in presentation: `modern` or `classic`. Nonempty ENV locks Setup → General; saved account/browser choices take priority. Leave empty to choose it in Setup. |
| `CALENDAR_SUBSCRIPTIONS_ENABLED` | ENV | `true` | Allow external HTTPS calendar creation/replacement and refresh. Set `false` and recreate the backend to stop remote fetching; cached events, local edits and tombstone cleanup remain available. Not editable in setup. |
| `MAX_UPLOAD_SIZE_MB` | BOTH | `15` | Image and general upload limit in MB. |
| `MAX_DOC_SIZE_MB` | BOTH | `500` | Limit in MB for each file added to a game's Docs tab, of any file type. |
| `MAX_SAVE_ARCHIVE_SIZE_MB` | BOTH | `4096` | Game save archive limit in MB. |
| `MAX_CLIP_SIZE_MB` | BOTH | `500` | Clip upload limit in MB. |
| `MAX_WORLD_SAVE_SIZE_MB` | BOTH | `2000` | Game world-save limit in MB. |

### Startup modes

`STARTUP_MODE=dev` or `development` selects development defaults and skips the setup/configuration UI after installation.

`STARTUP_MODE=testing` shows the setup UI and uses testing defaults where defined, otherwise development defaults.

An empty or unrecognized value uses normal defaults and shows the setup UI.

Explicit environment values still take precedence over these mode defaults.

## Upgrade recovery

These variables are environment-only (they are not shown in the setup UI, but they are real settings).

| Variable | Default | Purpose |
|---|---|---|
| `MIGRATION_BACKUP_MODE` | `automatic` | `automatic` creates and verifies an encrypted local recovery point before a pending migration touches an existing database, so a normal Portainer update or stack redeploy needs no extra step. `required` refuses to migrate without a full deployment recovery point issued by the operator backup workflow. `development` skips the check and is only for disposable data. |
| `MIGRATION_BACKUP_MAX_AGE_SECONDS` | `7200` | Maximum age (60 to 604800 seconds) of an operator-issued recovery point when `required` (or an operator receipt in `automatic`) is used. |

What automatic mode captures, where it stores it, retention, storage needs, how to protect the repository password and how to recover are in [Migration inspection and recovery](../deployment/migration-recovery.md#automatic-startup-recovery-default). The full-deployment workflow is in [Backups](../deployment/backups.md).

## First administrator

These values are used for environment-based bootstrap of the initial administrator:

| Variable | Source | Purpose |
|---|---|---|
| `PRIMARY_USER_USERNAME` | SETUP/BOTH | Initial administrator username: 1–100 ASCII letters, digits, underscores, hyphens or dots; no spaces. Existing bootstrap accounts are preserved. |
| `PRIMARY_USER_EMAIL` | SETUP/BOTH | Initial administrator email. |
| `PRIMARY_USER_PASSWORD` | SETUP/BOTH | Initial administrator password. |

The normal setup flow can collect these values instead of providing them through the environment.

## Password hashing

Environment-only settings for the Argon2id parameters used for new and upgraded local password hashes. They are not shown in the setup UI. Details, sizing guidance and how existing scrypt accounts are upgraded are in [Password hashing](../deployment/authentication-protection.md#password-hashing).

| Variable | Default | Range | Purpose |
|---|---|---|---|
| `ARGON2_MEMORY_KIB` | `19456` | 8192–262144 | Memory per password hash, in KiB. Total use scales with `AUTH_PASSWORD_WORKERS`. |
| `ARGON2_TIME_COST` | `2` | 1–10 | Argon2id iterations. |
| `ARGON2_PARALLELISM` | `1` | 1–8 | Argon2id lanes. |

## Application URL

| Variable | Default | Purpose |
|---|---|---|
| `PUBLIC_APP_URL` | Automatic per-user origin | Optional HTTPS public FQDN used after notification URL preferences and before each user's last-used app URL. ENV takes precedence over the setup/Application setting. Independent of OIDC; credentials, paths, queries and fragments are rejected. |

Each signed-in user has an independently remembered app origin. Users can override notification links globally or per external destination, including separate email and webhook domains. Public DNS for the configured FQDN is managed outside the app.

## Production Nginx TLS

Configure TLS and trusted proxies under **Settings → Administration → Application** or setup. The production container validates and gracefully reloads Nginx immediately after a successful save. Invalid certificate/configuration changes retain the working configuration. Development saves apply when used in production; it has no embedded Nginx listener. Environment values lock corresponding fields and require container recreation to change.

| Variable | Default | Purpose |
|---|---|---|
| `NGINX_TLS_ENABLED` | `false` | Enable production HTTPS; publish port 443 first. |
| `NGINX_TLS_REDIRECT_HTTP` | `false` | Redirect HTTP to HTTPS; requires TLS and reachable external HTTPS. |
| `NGINX_TLS_REDIRECT_PORT` | `443` | Environment only. The HTTPS port the redirect sends browsers to, when the published port is not 443. The example Compose files set it from `UNNAMED_TRACKING_APP_HTTPS_PORT`. |
| `NGINX_TLS_CERTIFICATE` | Empty | Absolute mounted PEM certificate/chain path. |
| `NGINX_TLS_PRIVATE_KEY` | Empty | Absolute mounted PEM private-key path, supplied with the certificate. |

Leave both paths blank to use the conventional mounted pair or a generated localhost certificate. Saving TLS settings activates renewed mounted certificates even when their paths are unchanged; there is no separate reload button. See [production Docker](../deployment/production-docker.md) for mounts, fallback and proxy trust behavior.

## Email notifications

SMTP is built into the host and uses the central registry with ENV precedence over administrator settings. Nonempty ENV values lock the corresponding UI fields. User recipient addresses and their verification are separate personal destinations.

| Variable | Purpose |
|---|---|
| `SMTP_HOST` | SMTP server hostname or literal IP. |
| `SMTP_PORT` | Optional custom port; unset uses TLS 465, STARTTLS 587, or plaintext 25. |
| `SMTP_TLS_MODE` | `starttls` (default), `ssl` for implicit TLS, or `none`. |
| `SMTP_FROM_ADDRESS` | Single sender address. |
| `SMTP_USERNAME` | Optional authentication username. |
| `SMTP_PASSWORD` | Optional authentication password. Stored UI values are encrypted and never returned. |

Missing a usable notification URL does not disable SMTP, but unsubscribe links are unavailable. SMTP plaintext warns; sensitive/recovery/verification messages require TLS except development or a literal local SMTP endpoint. See [notification providers](../user-guide/notification-providers.md) for the exact transport policy, verification and unsubscribe lifecycle. Password-reset and invite features remain plugins.

## Metadata/provider credentials

These registry fields can be supplied through the environment and are deployment-owned when an environment value is present:

| Variable | Purpose |
|---|---|
| `STEAMGRIDDB_API_KEY` | SteamGridDB game artwork/metadata access. |
| `RETROACHIEVEMENTS_API_KEY` | RetroAchievements data access. |
| `GIANTBOMB_API_KEY` | GiantBomb game metadata access. |
| `IGDB_CLIENT_ID` | IGDB client ID. |
| `IGDB_CLIENT_SECRET` | IGDB client secret. |
| `TMDB_API_KEY` | TMDB movie/TV metadata access. |
| `OMDB_API_KEY` | OMDb movie/TV metadata access. |
| `TVDB_API_KEY` | TheTVDB metadata access. |
| `SCREENSCRAPER_DEVID` | ScreenScraper developer ID. |
| `SCREENSCRAPER_DEVPASSWORD` | ScreenScraper developer password. |
| `SCREENSCRAPER_SSID` | ScreenScraper session ID. |
| `SCREENSCRAPER_SSPASSWORD` | ScreenScraper session password. |

## Account & data integrations

These integrations are used for external account connections, user-specific data, libraries, achievements, or importing data rather than general media metadata.

| Variable | Description |
|---|---|
| `XBOX_CLIENT_ID` | Xbox integration client ID. |
| `XBOX_CLIENT_SECRET` | Xbox integration client secret. |
| `GIANTBOMB_API_KEY` | API key used for GiantBomb integration and related game data. |
| `RETROACHIEVEMENTS_API_KEY` | API key used to access RetroAchievements data. |

Secret fields are not returned as plaintext by the setup configuration API.

## OpenID Connect / SSO

| Variable | Default | Purpose |
|---|---|---|
| `OIDC_PROVIDER_NAME` | `Provider 1` | Name of the setup-created provider. |
| `OIDC_PROVIDER_SLUG` | `provider-1` | URL-safe provider identifier used by named-provider routes. |
| `OIDC_ISSUER_URL` | — | OIDC issuer or discovery URL. |
| `OIDC_CLIENT_ID` | — | OIDC client ID. |
| `OIDC_CLIENT_SECRET` | — | OIDC client secret. |
| `OIDC_REDIRECT_URI` | Generated when omitted | Optional deployment-provided callback URI; otherwise the current request is used. |
| `OIDC_SCOPES` | `openid profile email` | Space-separated OIDC scopes. |
| `OIDC_GROUPS_CLAIM` | `groups` | Claim containing group membership. |
| `OIDC_ADMIN_GROUP` | — | Optional group whose members receive administrator access. |
| `OIDC_USER_MATCH_FIELD` | `email` | Match an OIDC identity by `email` or `username`. |
| `OIDC_ALLOW_NEW_USERS` | `true` | Allow unmatched OIDC identities to create local accounts. |
| `OIDC_REQUIRE_VERIFIED_EMAIL` | `false` | Require an explicit boolean `email_verified=true` for the matching bootstrap/environment provider and its callback aliases. False, missing or malformed claims are rejected when enabled. Named providers can enable this independently. |
| `OIDC_DEFAULT_LOGIN_METHOD` | `local` | Default login choice: `local` or `sso`. |
| `OIDC_LOGIN_BUTTON_TEXT` | `Continue with SSO` | Text used for the SSO login button. |

OIDC issuer, client ID, and client secret become required when the OIDC section is selected. See [OIDC / SSO](../user-guide/oidc.md) for the callback and account-linking behavior.

## Plugin Runtime

| Variable | Default | Purpose |
|---|---|---|
| `PLUGIN_RUNTIME_URL` | `http://plugin-runtime:8000` | Internal application-to-runtime URL. Do not publish it. |
| `PLUGIN_RUNTIME_TOKEN` | — | Shared internal transport token; production values must be unique and at least 32 characters. |
| `PLUGIN_GATEWAY_URL` | — | Internal app address reachable from plugin-runtime. An explicit runtime value wins; otherwise an explicitly configured app value is advertised over authenticated transport. Production Compose uses `http://app`, development uses `http://backend:8000`. |
| `PLUGIN_TRUSTED_PUBLISHER_REGISTRY` | Built-in registry | Optional path to an additional reviewed publisher registry. |
| `PLUGIN_CATALOG_URL` | Official repository `list.json` | Default official plugin catalogue URL. |
| `PLUGIN_CATALOGUE_REGISTRY` | `/data/plugin-catalogues.json` | Persistent administrator-managed catalogue metadata. |
| `PLUGIN_RUNTIME_DISCORD_EGRESS` | `false` in the runtime | Enables the runtime-owned, destination-validated Discord webhook sender used by the reference provider. It does not grant plugins direct networking. |
| `NONBUBBLE_ENV` | `false` | Development-only escape hatch that disables per-plugin bubblewrap isolation. Never enable for untrusted production plugins. |

## Frontend development

The frontend has one documented environment switch outside the backend registry:

| Variable | Purpose |
|---|---|
| `VITE_USE_MOCK_DATA` | When `true`, frontend services use their development/mock behavior instead of normal API-backed behavior. It is consumed by Vite and is not exposed through the backend setup schema. |

Do not add a `VITE_API_BASE_URL` setting unless the frontend code actually introduces one; the current frontend does not define it.

## Other environment variables in the registry

Remaining registry variables, for completeness. "Both" means the value can also be set in the UI and an environment value locks it.

| Variable | Source | Default | Purpose and details |
|---|---|---|---|
| `PASSWORD_MIN_LENGTH` | Both | `9` | Minimum length for new local passwords. See [Password policy](../development/password-policy.md). |
| `PASSWORD_REQUIRE_UPPERCASE` | Both | `true` | Require an uppercase letter. See [Password policy](../development/password-policy.md). |
| `PASSWORD_REQUIRE_LOWERCASE` | Both | `true` | Require a lowercase letter. See [Password policy](../development/password-policy.md). |
| `PASSWORD_REQUIRE_DIGIT` | Both | `false` | Require a number. See [Password policy](../development/password-policy.md). |
| `PASSWORD_REQUIRE_SYMBOL` | Both | `true` | Require a symbol. See [Password policy](../development/password-policy.md). |
| `NGINX_REALIP_HEADER` | Both | `X-Forwarded-For` | Header Nginx trusts for the client address when a request comes from a trusted proxy. See [Production Docker](../deployment/production-docker.md). |
| `NGINX_REALIP_TRUSTED_PROXIES` | Both | `127.0.0.1/32 ::1/128` | Space-separated addresses or CIDRs of proxies allowed to set that header. See [Production Docker](../deployment/production-docker.md). |
| `WEB_PUSH_VAPID_SUBJECT` | Both | Unset | Contact (`mailto:` or HTTPS URL) sent with browser push. See [Notification providers](../user-guide/notification-providers.md). |
| `WEB_PUSH_VAPID_PRIVATE_KEY` | Both | Unset | Private VAPID signing key for browser push; secret, never returned by the API. See [Notification providers](../user-guide/notification-providers.md). |
| `NOTIFICATION_RETENTION_DEFAULT_DAYS` | ENV | `30` | Default inbox history in days; `0` keeps it indefinitely. See [Notification centre](../user-guide/notification-centre.md). |
| `NOTIFICATION_RETENTION_MAXIMUM_DAYS` | ENV | `0` | Upper bound on inbox history in days; `0` means no maximum. See [Notification centre](../user-guide/notification-centre.md). |
| `NOTIFICATION_AUDIT_RETENTION_DAYS` | ENV | `90` | Days of metadata-only notification lifecycle history. See [Notification core](../development/notification-core.md). |
| `NOTIFICATION_BLOCKED_PROVIDERS` | ENV | Empty | Comma-separated provider IDs the deployment disallows. See [Notification core](../development/notification-core.md). |
| `NOTIFICATION_BLOCKED_TYPES` | ENV | Empty | Comma-separated event types the deployment disallows. See [Notification core](../development/notification-core.md). |
| `NOTIFICATION_MINIMUM_TRUST` | ENV | `0` | Minimum destination trust: `0` public, `1` private, `2` secure. See [Notification core](../development/notification-core.md). |
| `METADATA_HEALTH_INTERVAL_SECONDS` | ENV | `1800` | Seconds between non-blocking provider health checks (minimum 60). See [Metadata providers](../development/metadata-providers.md). |
| `GEOIP_DATABASE_PATH` | ENV | `/data/GeoIP.mmdb` | Location of the optional GeoIP database file. |
| `GEOIP_COUNTRY_DATABASE_PATH` | ENV | `/data/GeoIP-Country.mmdb` | Location of the optional GeoIP country database file. |
| `GEOIP_ASN_DATABASE_PATH` | ENV | `/data/GeoIP-ASN.mmdb` | Location of the optional GeoIP ASN database file. |

## Runtime variables outside the registry

Two backend runtime variables are read directly rather than being configuration-registry fields:

| Variable | Default | Purpose |
|---|---|---|
| `APP_DATA_DIR` | `/data` | Root directory used for the persistent generated Fernet key. |
| `ENV_FILE` | Searches for `.env` from the current directory upward | Explicit path to the environment file used by the configuration handler. |

## Docker Compose variables

These are Compose interpolation variables from `src/docker-container/compose.yaml`, not additional application configuration fields:

| Variable | Default / requirement | Purpose |
|---|---|---|
| `UNNAMED_TRACKING_APP_VERSION` | `latest` | Published image tag. |
| `UNNAMED_TRACKING_APP_PORT` | `8080` | Host port mapped to the application's container port 80. |
| `UNNAMED_TRACKING_APP_HTTPS_PORT` | `8443` | Host port mapped to container port 443 (used once HTTPS is enabled), and the default HTTPS redirect port. |
| `POSTGRES_USER` | `unnamed_tracking` in the production Compose file | Database container username. |
| `POSTGRES_DB` | `unnamed_tracking` in the production Compose file | Database container database name. |
| `POSTGRES_PASSWORD` | Required | Database container password. |
| `SECRET_KEY` | Required by the production Compose file | Application encryption/session key. |
| `PRIMARY_USER_USERNAME` | `admin` | Production Compose bootstrap username. |
| `PRIMARY_USER_EMAIL` | `admin@example.invalid` | Production Compose bootstrap email. |
| `PRIMARY_USER_PASSWORD` | Required | Production Compose bootstrap password. |
| `AUTH_COOKIE_SECURE` | `false` | Production Compose cookie security setting. |

The production Compose file builds `DATABASE_URL` from the PostgreSQL variables, so the application receives a complete database URL even though the Compose file does not pass `POSTGRES_HOST` or `POSTGRES_PORT` separately.

## Secrets

Never commit real credentials to `example.env`, the wiki, or source control.

For `SECRET_KEY`, omitting the variable is supported by the backend: a stable Fernet key is generated under `APP_DATA_DIR/config/fernet.key` with redundant copies and recovered on later starts. If you provide a deployment key, preserve it for the lifetime of the installation because existing encrypted values depend on it.

An explicit key is validated before use or persistence. The effective deployment value
comes from the process environment, then the `.env` selected by the configuration handler
(`ENV_FILE` when specified). A malformed value stops startup without printing the key.
For an existing installation, restore its original matching key from backup; generating
a new key cannot recover already encrypted credentials. Only a fresh installation with
no saved data should omit an invalid explicit value and let the backend generate a key.

Missing or damaged redundant copies are repaired from a valid matching copy. If valid
copies disagree, or an explicit key disagrees with a saved valid key, automatic recovery
stops without overwriting them. Preserve the files and restore the original matching
deployment key and copies from backup. If all saved copies are invalid, no replacement
is generated automatically. Storage errors identify `APP_DATA_DIR/config`; check the
persistent volume and its read/write permissions before restarting.

The four `MAX_*_SIZE_MB` upload caps can also be overridden in **Settings → Administration → Limits**. These deployment-wide overrides take effect on new uploads immediately; resetting removes the overrides and returns to the environment values.
