# API access matrix

This page is the route-by-route authorization audit of the **ordinary application
API**: every route the host serves, the credential class FastAPI enforces for it,
and what that credential may reach. It complements the
[scoped delegation matrix](plugin-public-apps.md#scoped-delegation-enforcement-matrix),
which documents the integration boundary in detail, and the user-facing
[User-approved access](../integrations/user-delegation.md) guide.

The table is checked by `tests/test_route_authorization_audit.py`: a route that is
added, removed or whose enforced authority changes fails the build until this page
is updated. The authority column is derived from the dependencies FastAPI actually
runs, not from this text.

## Credential classes

| Authority | Accepted credential | Never accepted |
| --- | --- | --- |
| `public` | None. Pre-login, discovery, static or single-use-proof endpoints | - |
| `user` | Browser session cookie or a legacy `utk_` API key, resolved to an active user | `utd_` delegated, `utp_` public-app and `utpm_` management credentials (403), except on `user-or-app` routes |
| `admin` | A `user` whose account is an administrator | The same exclusions; ordinary users get 403 |
| `browser-session` | Browser session only (consent, Connected apps) | API keys and every integration credential |
| `delegated-grant` | `utd_` credential with the route's scope, live grant, enabled client and active account | Browser sessions, `utk_` keys |
| `user-or-app` | A `user` credential, or a `utd_` app grant that carries the route's scope (`library.sync`, `saves.read`, `saves.write`, `content.write`) for a live grant, allowed client and active account | `utp_`, `utpm_`; a `utd_` grant without the scope (403) or that is unknown, revoked or switched off (401) |
| `plugin-admin` / `plugin-reader` | Administrator session/key, or a scoped `utpm_` management credential | Ordinary users (403), `utd_`/`utp_` |
| `plugin-runtime` | Short-lived runtime credential; calls pass the capability gateway | Browser and API keys |
| `plugin-route` | The plugin's declared route authentication, enforced before dispatch | - |
| `public-app-session` | A live `utp_` external-actor session for that installation | Host accounts, API keys |
| `setup-or-admin` | No credential only while the instance has no users; then administrator | - |
| `local-or-admin` | Direct loopback caller without proxy headers (the container's nginx renderer), first-run setup, or administrator | Remote anonymous callers |

An administrator's delegated or public-app credential is still not an administrator
credential: `get_current_user` rejects `utd_`, `utp_` and `utpm_` before cookies or
ownership are evaluated.

## How the audit was performed

1. The route inventory is read from the running FastAPI application (every route
   method), so nothing relies on a hand-kept list.
2. Every route is classified by its dependency graph. Routes with inline
   authentication (`setup-or-admin`, `local-or-admin`) are listed explicitly.
3. For all object-addressed `user` routes, the victim's identifiers (game, movie,
   show, season, episode, list, list item, profile, archive, version, file, note,
   screenshot, notification, destination, calendar event/subscription, rewatch, activity, session,
   API key, device credential, user) are substituted into the URL and called with a
   different ordinary user's credential, with schema-valid bodies. Everything must be
   403/404 (or a documented idempotent no-op that leaves the victim's rows intact).
4. Identifiers carried in bodies and queries (bulk game updates, parent game, list
   items and order, rewatch/activity media, episode batches) are tested separately.
5. Every `admin`/`plugin-admin` route is called by an ordinary user, every `user`
   route with each integration credential prefix and every authenticated route with
   no credential.

## Findings and fixes

| Finding | Fix |
| --- | --- |
| `POST /api/plugin-permissions/requests` let any signed-in user queue an installation-wide permission proposal in the administrator review queue. | Only administrators may raise installation-wide proposals; ordinary users may still propose for their own scope. |
| `GET /api/internal/real-ip` returned the trusted-proxy header and CIDR list to anonymous remote callers. | Served to the local nginx renderer (direct loopback, no proxy headers), to first-run setup and to administrators. Presets and validation stay public because they are inert. |
| Uvicorn's access log recorded the calendar feed token in the URL path and the OIDC callback `code`/`state` query. | The existing access-log filter now redacts both, in addition to the notification link tokens it already hid. |

No other object-level gap was found: every path-addressed ordinary route resolves the
object through an owner-scoped lookup (`user_id == caller`) and child objects must
belong to their parent. Body-carried IDs are filtered the same way.

Remaining by-design behaviors, recorded so they are not mistaken for gaps:

- A request-body validation failure (422) can run before an ownership lookup. The
  sweep tolerates a small, bounded set of such routes and the targeted tests above
  cover the writes that matter.
- Provider, job and AniList identifiers name instance catalogue entries rather than
  user rows. Per-user state behind them is keyed by the authenticated caller.
- `DELETE /api/notifications/{id}` and `POST /api/notifications/{id}/dismiss` return
  204 for an identifier the caller cannot see and change nothing.

## Legacy API keys

Legacy `utk_` keys are **unrestricted for their owner**: they carry exactly the
routes and administrator status of the user who created them. The `scopes` list
accepted when a key is created is stored as a label only; it is not enforced. This
behaviour is intentionally unchanged so that existing clients keep working, and
nothing narrows or upgrades a legacy key automatically. Consequences:

- A key never exceeds its owner: a non-administrator's key gets 403 on administrator
  routes and 404 for another user's objects (regression-tested).
- A key cannot create keys (`POST /api/auth/api-keys` requires a browser session) and
  cannot manage consent grants (`browser-session` routes).
- Revoking a key, deactivating its owner or deleting the owner stops it on the next
  request.
- Clients that need least privilege should migrate to the
  [user-approved access](../integrations/user-delegation.md) protocol, which issues
  scoped, expiring, revocable `utd_` credentials. Any future narrowing of legacy
  keys would be a separate, announced migration.

## Account creation and guest claiming

Accounts are created only through: first-run setup (409 once any user exists), an
administrator (`POST /api/auth/users`), an accepted plugin-delivered invitation, or
OIDC sign-in when the provider allows new users. No route accepts a delegated,
public-app or legacy credential as authority to register or claim an account. A
public-app (Discord) guest is an external actor with its own session; signing in
creates no `User` row, and there is no claim route. The only registration-shaped
route, `POST /api/delegation/v1/clients/register`, registers a requester *origin*
(when the owner enables open origins) and grants no data or account.

## Logging and credentials

Credentials are carried in headers or request bodies, never in URLs, with two
unavoidable exceptions that are redacted from the application access log: the
calendar feed token (`/api/calendar/feed/{token}.ics`, a bearer URL by design for
calendar clients) and single-use notification links. OIDC `code`/`state` query values
are redacted. Validation errors never echo submitted values.

## Routes

### Bootstrap, discovery and static assets

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | public | Credential check; sets/clears the caller's own session cookie. Rate limited. |
| POST | `/api/auth/logout` | public | Credential check; sets/clears the caller's own session cookie. Rate limited. |
| GET | `/api/auth/oidc/callback` | public | OIDC sign-in; new accounts only when the provider allows new users. |
| GET | `/api/auth/oidc/callback/{provider_slug}` | public | OIDC sign-in; new accounts only when the provider allows new users. |
| GET | `/api/auth/oidc/login` | public | OIDC sign-in; new accounts only when the provider allows new users. |
| GET | `/api/auth/oidc/login/{provider_slug}` | public | OIDC sign-in; new accounts only when the provider allows new users. |
| GET | `/api/auth/oidc/status` | public | OIDC sign-in; new accounts only when the provider allows new users. |
| GET | `/api/auth/password-policy` | public | Read: public password rules. Write: administrator. |
| PUT | `/api/auth/password-policy` | admin | Administrator only. |
| POST | `/api/auth/password-reset/complete` | public | Single-use proof from the plugin-delivered link; confers no authority from an integration credential. |
| POST | `/api/auth/password-reset/request` | public | Single-use proof from the plugin-delivered link; confers no authority from an integration credential. |
| GET | `/api/auth/password-reset/status` | public | Single-use proof from the plugin-delivered link; confers no authority from an integration credential. |
| POST | `/api/auth/user-invites/accept` | public | Single-use proof from the plugin-delivered link; confers no authority from an integration credential. |
| GET | `/api/auth/user-invites/status` | public | Single-use proof from the plugin-delivered link; confers no authority from an integration credential. |
| GET | `/api/branding` | public | Instance-level or pre-login data; no per-user object is addressed. |
| PUT | `/api/branding` | admin | Administrator only. |
| DELETE | `/api/branding/assets/{kind}` | admin | Administrator only. |
| POST | `/api/branding/assets/{kind}` | admin | Administrator only. |
| GET | `/api/branding/assets/{kind}/{revision}.png` | public | Instance-level or pre-login data; no per-user object is addressed. |
| GET | `/api/branding/default-icon.svg` | public | Instance-level or pre-login data; no per-user object is addressed. |
| GET | `/api/currency-codes` | user | Lookup using provider data or the caller's configured credentials; no stored object is addressed. |
| GET | `/api/internal/real-ip` | local-or-admin | Trusted-proxy topology: direct loopback caller (nginx renderer), first-run setup, or administrator. |
| GET | `/api/internal/real-ip/presets` | public | Inert normalization/presets; reads no stored configuration. |
| POST | `/api/internal/real-ip/validate` | public | Inert normalization/presets; reads no stored configuration. |
| POST | `/api/setup` | public | First-run only; 409 once any user exists. |
| GET | `/api/setup/configuration` | setup-or-admin | Open only before the first user exists; afterwards administrator. |
| PUT | `/api/setup/configuration` | admin | Administrator only. |
| GET | `/api/setup/status` | public | Boolean first-run flag and startup mode; no account data. |
| GET | `/api/themes` | public | Instance-level or pre-login data; no per-user object is addressed. |
| GET | `/api/themes/assets/{theme_id}/{digest}/{asset_path:path}` | public | Instance-level or pre-login data; no per-user object is addressed. |
| GET | `/health` | public | Instance-level or pre-login data; no per-user object is addressed. |
| GET | `/manifest.webmanifest` | public | Instance-level or pre-login data; no per-user object is addressed. |
| GET | `/pwa/icons/{revision}/{size}.png` | public | Instance-level or pre-login data; no per-user object is addressed. |
| GET | `/pwa/offline.html` | public | Instance-level or pre-login data; no per-user object is addressed. |
| GET | `/pwa/status` | public | Instance-level or pre-login data; no per-user object is addressed. |
| GET | `/service-worker.js` | public | Instance-level or pre-login data; no per-user object is addressed. |

### Account, profile and API keys

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| GET | `/api/auth/api-keys` | user | Lists the caller's own keys (hashes never returned). |
| POST | `/api/auth/api-keys` | user | Create requires a signed-in browser session (an API key cannot mint keys). |
| DELETE | `/api/auth/api-keys/{key_id}` | user | Revokes only a key whose `user_id` is the caller. |
| GET | `/api/auth/me` | user | Own account only; `is_admin` is not in the request schema. |
| PATCH | `/api/auth/me` | user | Own account only; `is_admin` is not in the request schema. |
| DELETE | `/api/auth/me/psn` | user | Own account only; `is_admin` is not in the request schema. |
| POST | `/api/auth/me/psn` | user | Own account only; `is_admin` is not in the request schema. |
| GET | `/api/auth/me/psn/status` | user | Own account only; `is_admin` is not in the request schema. |
| GET | `/api/auth/users` | admin | Instance user management; the primary user and the caller cannot be demoted or deleted. |
| POST | `/api/auth/users` | admin | Instance user management; the primary user and the caller cannot be demoted or deleted. |
| DELETE | `/api/auth/users/{user_id}` | admin | Instance user management; the primary user and the caller cannot be demoted or deleted. |
| PATCH | `/api/auth/users/{user_id}` | admin | Instance user management; the primary user and the caller cannot be demoted or deleted. |
| GET | `/api/auth/user-invitations` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| DELETE | `/api/auth/user-invitations/{invitation_id}` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| POST | `/api/auth/user-invitations/{invitation_id}/resend` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| GET | `/api/auth/user-audit/{target_id}` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| GET | `/api/auth/users/{user_id}/email-destinations` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| POST | `/api/auth/users/{user_id}/email` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| POST | `/api/auth/users/{user_id}/email-verification` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| POST | `/api/auth/users/{user_id}/email-verification/revoke` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| POST | `/api/auth/users/{user_id}/force-password-reset` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| POST | `/api/auth/users/{user_id}/password` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| POST | `/api/auth/users/{user_id}/password-reset-link` | admin | Instance user administration; administrator only, never reachable with a delegated, public-app or legacy credential. |
| GET | `/api/user/{user_id}/profile-picture` | user | Self or administrator; another ordinary user's ID is 403. |
| DELETE | `/api/user/{user_id}/profile-picture` | user | Self or administrator; another ordinary user's ID is 403. |
| PUT | `/api/user/{user_id}/profile-picture` | user | Self or administrator; another ordinary user's ID is 403. |

### Integration delegation (`utd_`)

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| PUT | `/api/delegation/v1/clients` | admin | Administrator only. |
| POST | `/api/delegation/v1/clients/register` | public | Enabled open-origin policy: Bounded registration only; disabled client remains disabled |
| POST | `/api/delegation/v1/desktop/{app}/requests` | public | Known first-party desktop app and `allow_desktop_apps`: starts consent only; the user approves each capability in their browser |
| DELETE | `/api/delegation/v1/credential` | delegated-grant | Live delegated grant: Revokes only the presenting grant (an app signing out) |
| GET | `/api/delegation/v1/credential` | delegated-grant | Live delegated grant: Own scopes and expiry only |
| POST | `/api/delegation/v1/credential/rotate` | delegated-grant | Live delegated grant: Replace only the presented credential; scopes/expiry unchanged |
| GET | `/api/delegation/v1/games` | delegated-grant | `games.read`: Own nondeleted games; paginated 1–200 |
| GET | `/api/delegation/v1/games/{game_id}` | delegated-grant | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| POST | `/api/delegation/v1/games/{game_id}/achievement-reports` | delegated-grant | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| DELETE | `/api/delegation/v1/games/{game_id}/achievement-reports/{report_id}` | delegated-grant | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| GET | `/api/delegation/v1/games/{game_id}/achievements` | delegated-grant | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| GET | `/api/delegation/v1/games/{game_id}/artwork/{kind}` | delegated-grant | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| GET | `/api/delegation/v1/games/{game_id}/history` | delegated-grant | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| PATCH | `/api/delegation/v1/games/{game_id}/play-compatibility` | delegated-grant | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| PATCH | `/api/delegation/v1/games/{game_id}/tracking` | delegated-grant | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| GET | `/api/delegation/v1/grants` | browser-session | Browser session: Only this user's grant list |
| DELETE | `/api/delegation/v1/grants/{grant_id}` | browser-session | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| PATCH | `/api/delegation/v1/grants/{grant_id}` | browser-session | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| GET | `/api/delegation/v1/instance` | public | Public discovery: Minimized policy, stable instance ID and enabled requester identities; no account data |
| GET | `/api/delegation/v1/lists` | delegated-grant | `lists.read`: Own lists |
| GET | `/api/delegation/v1/lists/{list_id}` | delegated-grant | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| GET | `/api/delegation/v1/media` | delegated-grant | `media.read`: Own nondeleted movie/TV/anime projections; paginated |
| GET | `/api/delegation/v1/policy` | admin | Administrator only. |
| PUT | `/api/delegation/v1/policy` | admin | Administrator only. |
| GET | `/api/delegation/v1/profile` | delegated-grant | `profile.read`: Own ID/display name; no email, secrets or administrator fields |
| POST | `/api/delegation/v1/requests` | public | Enabled registered requester: Supported scopes and verifier challenge; no user selected yet |
| GET | `/api/delegation/v1/requests/{request_id}` | browser-session | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| POST | `/api/delegation/v1/requests/{request_id}/decision` | browser-session | See the scoped delegation table in [Public apps](plugin-public-apps.md). |
| POST | `/api/delegation/v1/requests/{request_id}/exchange` | public | See the scoped delegation table in [Public apps](plugin-public-apps.md). |

### Public apps (external actors)

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| POST | `/api/public-apps/{plugin_id}/action` | public-app-session | Same-installation external session required; no host account data. |
| GET | `/api/public-apps/{plugin_id}/app/` | public | Public packaged frontend/configuration of an enabled app; no account data. |
| GET | `/api/public-apps/{plugin_id}/app/{asset_path:path}` | public | Public packaged frontend/configuration of an enabled app; no account data. |
| POST | `/api/public-apps/{plugin_id}/catalogue` | public-app-session | Same-installation external session required; no host account data. |
| GET | `/api/public-apps/{plugin_id}/catalogue/{app_id}/artwork` | public-app-session | Same-installation external session required; no host account data. |
| GET | `/api/public-apps/{plugin_id}/config` | public | Public packaged frontend/configuration of an enabled app; no account data. |
| GET | `/api/public-apps/{plugin_id}/participants/{participant_id}/avatar` | public-app-session | Same-installation external session required; no host account data. |
| DELETE | `/api/public-apps/{plugin_id}/sessions` | public-app-session | POST: provider-verified proof creates an external actor session (no host account). DELETE: invalidates the presented session. |
| POST | `/api/public-apps/{plugin_id}/sessions` | public | POST: provider-verified proof creates an external actor session (no host account). DELETE: invalidates the presented session. |
| GET | `/api/public-apps/{plugin_id}/sources` | public-app-session | Sources are private to the verified actor (at most five). |
| POST | `/api/public-apps/{plugin_id}/sources` | public-app-session | Sources are private to the verified actor (at most five). |
| POST | `/api/public-apps/{plugin_id}/sources/legacy` | public-app-session | Sources are private to the verified actor (at most five). |
| DELETE | `/api/public-apps/{plugin_id}/sources/{source_id}` | public-app-session | Sources are private to the verified actor (at most five). |
| POST | `/api/public-apps/{plugin_id}/sources/{source_id}/games/{game_id}/achievement-reports` | public-app-session | Source must belong to the session's actor; the remote delegated API re-enforces ownership and scope. |
| GET | `/api/public-apps/{plugin_id}/sources/{source_id}/games/{game_id}/achievements` | public-app-session | Source must belong to the session's actor; the remote delegated API re-enforces ownership and scope. |
| GET | `/api/public-apps/{plugin_id}/sources/{source_id}/games/{game_id}/artwork/{kind}` | public-app-session | Source must belong to the session's actor; the remote delegated API re-enforces ownership and scope. |
| PATCH | `/api/public-apps/{plugin_id}/sources/{source_id}/games/{game_id}/play-compatibility` | public-app-session | Source must belong to the session's actor; the remote delegated API re-enforces ownership and scope. |
| PATCH | `/api/public-apps/{plugin_id}/sources/{source_id}/games/{game_id}/tracking` | public-app-session | Source must belong to the session's actor; the remote delegated API re-enforces ownership and scope. |
| POST | `/api/public-apps/{plugin_id}/sources/{source_id}/poll` | public-app-session | Sources are private to the verified actor (at most five). |
| POST | `/api/public-apps/{plugin_id}/sources/{source_id}/refresh` | public-app-session | Sources are private to the verified actor (at most five). |

### Games and game content

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| GET | `/api/game-identity/backfill` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| GET | `/api/game-identity/backfill/plan` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| GET | `/api/game-identity/games/{game_id}/overview` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| GET | `/api/game-identity/games/{game_id}/sources` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| GET | `/api/game-identity/library` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| GET | `/api/game-identity/review/automatic` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| GET | `/api/game-identity/review/queue` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| POST | `/api/game-identity/backfill` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| POST | `/api/game-identity/review/automatic/{decision_id}/keep` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| POST | `/api/game-identity/review/automatic/{decision_id}/undo` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| POST | `/api/game-identity/review/queue/{decision_id}/keep-separate` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| POST | `/api/game-identity/review/queue/{decision_id}/relate` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| POST | `/api/game-identity/review/queue/{decision_id}/merge` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| POST | `/api/game-identity/review/run` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| PUT | `/api/game-identity/relationships/{relationship_id}` | user | Own identity, review and backfill state; foreign identifiers are refused. |
| PUT | `/api/game-identity/relationships/{relationship_id}/display` | user | Own identity, review and backfill state; foreign identifiers are refused. |

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| DELETE | `/api/game-identity/games/{game_id}/completion` | user | Own manual tracking; foreign identifiers are refused. |
| DELETE | `/api/game-identity/manual-achievements/{achievement_id}` | user | Own manual tracking; foreign identifiers are refused. |
| GET | `/api/game-identity/games/{game_id}/achievements` | user | Own manual tracking; foreign identifiers are refused. |
| GET | `/api/game-identity/games/{game_id}/completion` | user | Own manual tracking; foreign identifiers are refused. |
| GET | `/api/game-identity/games/{game_id}/completion/history` | user | Own manual tracking; foreign identifiers are refused. |
| GET | `/api/game-identity/games/{game_id}/tracking` | user | Own manual tracking; foreign identifiers are refused. |
| GET | `/api/game-identity/manual-achievements/{achievement_id}/icon` | user | Own manual tracking; foreign identifiers are refused. |
| PATCH | `/api/game-identity/manual-achievements/{achievement_id}` | user | Own manual tracking; foreign identifiers are refused. |
| POST | `/api/game-identity/games/{game_id}/manual-achievements` | user | Own manual tracking; foreign identifiers are refused. |
| PUT | `/api/game-identity/games/{game_id}/completion` | user | Own manual tracking; foreign identifiers are refused. |
| PUT | `/api/game-identity/manual-achievements/{achievement_id}/earned` | user | Own manual tracking; foreign identifiers are refused. |

| Method | Route | Authority | Access |
| --- | --- | --- | --- |
| DELETE | `/api/game-identity/counters/{counter_id}/adjustment` | user | Own game identity state and source observations; foreign identifiers are refused. |
| GET | `/api/game-identity/games/{game_id}/metrics` | user | Own game identity state and source observations; foreign identifiers are refused. |
| GET | `/api/game-identity/names/games/{game_id}` | user | Own game identity state and source observations; foreign identifiers are refused. |
| GET | `/api/game-identity/names/reviews` | user | Own game identity state and source observations; foreign identifiers are refused. |
| GET | `/api/playnite/contract` | user-or-app | Own game identity state and source observations; foreign identifiers are refused. App grants need `library.sync`. |
| POST | `/api/game-identity/counters/{counter_id}/group` | user | Own game identity state and source observations; foreign identifiers are refused. |
| POST | `/api/game-identity/counters/{counter_id}/members/{observation_id}/ungroup` | user | Own game identity state and source observations; foreign identifiers are refused. |
| POST | `/api/game-identity/names/repair` | user | Own game identity state and source observations; foreign identifiers are refused. |
| POST | `/api/game-identity/names/reviews/{review_id}/keep` | user | Own game identity state and source observations; foreign identifiers are refused. |
| POST | `/api/game-identity/names/reviews/{review_id}/update` | user | Own game identity state and source observations; foreign identifiers are refused. |
| POST | `/api/playnite/inventory` | user-or-app | Own game identity state and source observations; foreign identifiers are refused. App grants need `library.sync`. |
| POST | `/api/playnite/observations` | user-or-app | Own game identity state and source observations; foreign identifiers are refused. App grants need `library.sync`. |
| PUT | `/api/game-identity/counters/{counter_id}/members/{observation_id}/priority` | user | Own game identity state and source observations; foreign identifiers are refused. |
| PUT | `/api/game-identity/games/{game_id}/metrics/playtime` | user | Own game identity state and source observations; foreign identifiers are refused. |

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| GET | `/api/achievement-icon/{achievement_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/achievement-icon/{achievement_id}/locked` | user | Same ownership join as the unlocked icon; serves the provider's greyed-out icon. |
| GET | `/api/game-duplicates` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game-duplicates/keep-both` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game-identity/decisions` | user | Every game, observation, counter and relationship is looked up with `user_id == caller`; another user's ID is not found. |
| GET | `/api/game-identity/games/{game_id}` | user | Every game, observation, counter and relationship is looked up with `user_id == caller`; another user's ID is not found. |
| POST | `/api/game-identity/merge` | user | Every game, observation, counter and relationship is looked up with `user_id == caller`; another user's ID is not found. |
| POST | `/api/game-identity/games/{game_id}/make-main` | user | Every game in the group is looked up with `user_id == caller`; another user's ID is not found. |
| POST | `/api/game-identity/games/{game_id}/split` | user | Every game in the group is looked up with `user_id == caller`; another user's ID is not found. |
| POST | `/api/game-identity/observations/{observation_id}/move` | user | Every game, observation, counter and relationship is looked up with `user_id == caller`; another user's ID is not found. |
| POST | `/api/game-identity/relationships` | user | Every game, observation, counter and relationship is looked up with `user_id == caller`; another user's ID is not found. |
| POST | `/api/game-identity/relationships/{relationship_id}/end` | user | Every game, observation, counter and relationship is looked up with `user_id == caller`; another user's ID is not found. |
| POST | `/api/game-identity/separate` | user | Every game, observation, counter and relationship is looked up with `user_id == caller`; another user's ID is not found. |
| POST | `/api/game-identity/unmerge` | user | Every game, observation, counter and relationship is looked up with `user_id == caller`; another user's ID is not found. |
| POST | `/api/game-ownership/group` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game-ownership/separate` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/achievements-summary` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| PATCH | `/api/game/bulk-update` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/create` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `library.sync`. |
| GET | `/api/game/collection-index` | user-or-app | Compact projection of the caller's own nondeleted games. App grants need `library.sync`. |
| GET | `/api/lists/{list_id}/cover` | user | The caller's own media collection and owned poster sources only. |
| PUT | `/api/lists/{list_id}/cover` | user | Uploads only the caller's own media collection cover. |
| DELETE | `/api/lists/{list_id}/cover` | user | Removes only the caller's own custom media collection cover. |
| GET | `/api/game/collection-cover` | user-or-app | Cover belongs to the caller; every source game must belong to that account. App grants need `library.sync`. |
| PUT | `/api/game/collection-cover` | user-or-app | Uploads only the caller's own collection cover. App grants need `library.sync`. |
| DELETE | `/api/game/collection-cover` | user-or-app | Removes only the caller's own custom collection cover. App grants need `library.sync`. |
| POST | `/api/game/collection-cover/rename` | user-or-app | Renames only the caller's own custom collection cover. App grants need `library.sync`. |
| DELETE | `/api/game/delete/{game_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/get/{game_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/list` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `library.sync`. |
| GET | `/api/game/metadata/search` | user | Lookup using provider data or the caller's configured credentials; no stored object is addressed. |
| GET | `/api/game/notes/search` | user | Searches only the caller's own games' notes. |
| GET | `/api/game/preview-cover` | user | Lookup using provider data or the caller's configured credentials; no stored object is addressed. |
| GET | `/api/game/trash` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| PATCH | `/api/game/update/{game_id}` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `library.sync`. |
| GET | `/api/game/{game_id}/achievements` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| DELETE | `/api/game/{game_id}/achievements/{achievement_id}/progress` | user | Game and achievement are queried with `user_id == caller`; only that user's manual progress override is removed. |
| PUT | `/api/game/{game_id}/achievements/{achievement_id}/progress` | user | Game and achievement are queried with `user_id == caller`; only that user's manual progress override is changed. |
| DELETE | `/api/game/{game_id}/archives/{archive_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| PATCH | `/api/game/{game_id}/archives/{archive_id}` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `saves.write`. |
| POST | `/api/game/{game_id}/archives/{archive_id}/restore` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/archives/{archive_id}/versions` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `saves.write`. |
| DELETE | `/api/game/{game_id}/archives/{archive_id}/versions/{version_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/archives/{archive_id}/versions/{version_id}/download` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `saves.read`. |
| POST | `/api/game/{game_id}/archives/{archive_id}/versions/{version_id}/restore` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/archives/{kind}` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `saves.read`. |
| POST | `/api/game/{game_id}/archives/{kind}` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `saves.write`. |
| GET | `/api/game/{game_id}/archives/{kind}/trash` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/assets/{asset_kind}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/assets/{asset_kind}` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `library.sync`. |
| POST | `/api/game/{game_id}/assets/{asset_kind}/from-url` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/checklist` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/checklist` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| PUT | `/api/game/{game_id}/checklist/reorder` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| DELETE | `/api/game/{game_id}/checklist/{item_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| PATCH | `/api/game/{game_id}/checklist/{item_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/content-counts` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/field-changes` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/files/{kind}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/files/{kind}` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `content.write` and may add `doc` only; `modpack` is refused (403). |
| PATCH | `/api/game/{game_id}/files/{kind}/by-id/{item_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/files/{kind}/trash` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| DELETE | `/api/game/{game_id}/files/{kind}/{filename}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/files/{kind}/{filename}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/files/{kind}/{filename}/restore` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/metadata/refresh` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/notes` | user-or-app | Own game notes only; app grants need `notes.read`. |
| GET | `/api/game/{game_id}/notes-summary` | user-or-app | Own game notes only; app grants need `notes.read`. |
| DELETE | `/api/game/{game_id}/notes/{note_name}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/notes/{note_name}` | user-or-app | Own game note only; app grants need `notes.read`. |
| POST | `/api/game/{game_id}/notes/{note_name}` | user-or-app | Own game note only; app grants need `notes.write`. Existing names are never replaced. |
| PUT | `/api/game/{game_id}/notes/{note_name}` | user-or-app | Own game note only; app grants need `notes.write`. A stale `expected_sha256` returns 409 without replacing the body. |
| PATCH | `/api/game/{game_id}/notes/{note_name}/details` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/notes/{note_name}/duplicate` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/notes/{note_name}/move` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| PATCH | `/api/game/{game_id}/notes/{note_name}/rename` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/notes/{note_name}/versions` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/notes/{note_name}/versions/{version_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/notes/{note_name}/versions/{version_id}/restore` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/profiles` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/profiles` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/profiles/trash` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| DELETE | `/api/game/{game_id}/profiles/{profile_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| PATCH | `/api/game/{game_id}/profiles/{profile_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/profiles/{profile_id}/restore` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/profiles/{profile_id}/stat-history` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/profiles/{profile_id}/sync-wiseoldman` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/restore` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/screenshots` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/screenshots` | user-or-app | Game queried with `user_id == caller`; any child ID must also belong to that game. App grants need `content.write`. |
| POST | `/api/game/{game_id}/screenshots/detect-dates` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/screenshots/trash` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| DELETE | `/api/game/{game_id}/screenshots/{kind}/{filename}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/screenshots/{kind}/{filename}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/screenshots/{kind}/{filename}/restore` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| PATCH | `/api/game/{game_id}/screenshots/{media_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/thumbnails/{media_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| POST | `/api/game/{game_id}/thumbnails/{media_id}` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/variants` | user | Game queried with `user_id == caller`; any child ID must also belong to that game. |
| GET | `/api/game/{game_id}/world-map/{retired_path:path}` | user | Retired feature; owner-checked tombstone. |
| POST | `/api/game/{game_id}/world-map/{retired_path:path}` | user | Retired feature; owner-checked tombstone. |
| POST | `/api/library-sync/games/{game_id}/achievements` | user | Game must be owned by the caller. |

### Movies, TV, anime and tracking

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| GET | `/api/activity` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| POST | `/api/activity` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| DELETE | `/api/activity/{entry_id}` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| PATCH | `/api/activity/{entry_id}` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| POST | `/api/anime/create` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| DELETE | `/api/anime/delete/{show_id}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/anime/fill-titles` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/anime/get/{show_id}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/anime/import/anilist` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/anime/list` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/anime/metadata/by-id/{anilist_id}` | user | Lookup using provider data or the caller's configured credentials; no stored object is addressed. |
| GET | `/api/anime/metadata/search` | user | Lookup using provider data or the caller's configured credentials; no stored object is addressed. |
| GET | `/api/anime/trash` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/anime/trash/empty` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| PATCH | `/api/anime/update/{show_id}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| DELETE | `/api/anime/{show_id}/purge` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/anime/{show_id}/recommended` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/anime/{show_id}/refresh-airing` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/anime/{show_id}/relations` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/anime/{show_id}/restore` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/anime/{show_id}/seasons` | user | Show owned by caller; season/episode must belong to that show. |
| DELETE | `/api/anime/{show_id}/seasons/{season_id}` | user | Show owned by caller; season/episode must belong to that show. |
| PATCH | `/api/anime/{show_id}/seasons/{season_id}` | user | Show owned by caller; season/episode must belong to that show. |
| GET | `/api/anime/{show_id}/seasons/{season_id}/episodes` | user | Show owned by caller; season/episode must belong to that show. |
| PATCH | `/api/anime/{show_id}/seasons/{season_id}/episodes/bulk-watched` | user | Show owned by caller; season/episode must belong to that show. |
| PATCH | `/api/anime/{show_id}/seasons/{season_id}/episodes/{episode_id}` | user | Show owned by caller; season/episode must belong to that show. |
| GET | `/api/calendar` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/calendar/events` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| POST | `/api/calendar/events` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| DELETE | `/api/calendar/events/{event_id}` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| PATCH | `/api/calendar/events/{event_id}` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| GET | `/api/calendar/feed-token` | user | Own feed token. |
| POST | `/api/calendar/feed-token/regenerate` | user | Own feed token. |
| GET | `/api/calendar/feed/{token}.ics` | public | Possession of the unguessable per-user feed token; token is redacted from access logs. |
| GET | `/api/calendar/feed/{token}/{kind}.ics` | public | The same per-user feed token, narrowed to one layer (`anime`, `tv`, `movie`, `game`, `manual`); regenerating the token ends every layer's link. |
| GET | `/api/calendar/games` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/calendar/sources` | user | Lists only subscriptions owned by the caller; secret URLs are never returned. |
| POST | `/api/calendar/sources` | user | Creates a subscription for the caller, subject to deployment policy and per-account limits. |
| PATCH | `/api/calendar/sources/{source_id}` | user | Source resolved by caller ownership before updates or replacement URL work. |
| DELETE | `/api/calendar/sources/{source_id}` | user | Deletes only a source owned by the caller and its imported/override rows. |
| POST | `/api/calendar/sources/{source_id}/refresh` | user | Source resolved by caller ownership before network work. |
| GET | `/api/calendar/sources/{source_id}/events` | user | Reads cached events and overlays only for a source owned by the caller. |
| PUT | `/api/calendar/sources/{source_id}/overrides` | user | Owned source and current upstream occurrence required; edits stay local. |
| DELETE | `/api/calendar/sources/{source_id}/overrides` | user | Owned source and current upstream occurrence required; clears only the selected scope. |
| POST | `/api/calendar/sources/{source_id}/copy` | user | Copies a current occurrence from an owned source into the caller's manual events. |
| GET | `/api/export/backup-status` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| GET | `/api/export/library` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| GET | `/api/export/media.csv` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| POST | `/api/import/library` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| POST | `/api/import/list` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| POST | `/api/import/list/preview` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| POST | `/api/import/mal` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| POST | `/api/import/mal/preview` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| POST | `/api/import/media` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| POST | `/api/import/yamtrack` | user | Only the caller's library; imported rows are stamped with the caller's ID. |
| POST | `/api/library-sync/psn` | user | Uses the caller's stored provider credentials. |
| POST | `/api/library-sync/retroachievements` | user | Uses the caller's stored provider credentials. |
| POST | `/api/library-sync/steam` | user | Uses the caller's stored provider credentials. |
| POST | `/api/library-sync/steam-tags/refresh` | user | Uses the caller's stored provider credentials. |
| POST | `/api/library-sync/steam/achievements` | user | Uses the caller's stored provider credentials. |
| POST | `/api/library-sync/steam/enrich` | user | Uses the caller's stored provider credentials. |
| POST | `/api/library-sync/steam/wishlist` | user | Uses the caller's stored provider credentials. |
| GET | `/api/lists` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/lists` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/lists/membership` | user | List owned by caller; added media must also be the caller's. |
| PUT | `/api/lists/order` | user | List owned by caller; added media must also be the caller's. |
| DELETE | `/api/lists/{list_id}` | user | List owned by caller; added media must also be the caller's. |
| GET | `/api/lists/{list_id}` | user | List owned by caller; added media must also be the caller's. |
| PATCH | `/api/lists/{list_id}` | user | List owned by caller; added media must also be the caller's. |
| POST | `/api/lists/{list_id}/items` | user | List owned by caller; added media must also be the caller's. |
| DELETE | `/api/lists/{list_id}/items/{item_id}` | user | List owned by caller; added media must also be the caller's. |
| PUT | `/api/lists/{list_id}/order` | user | List owned by caller; added media must also be the caller's. |
| GET | `/api/media` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/media-image/{media_type}/{item_id}/{which}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/media-stats` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/media-stats/activity/day` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/media/inbox` | user | Inbox file belongs to the caller's storage and row. |
| POST | `/api/media/inbox` | user | Inbox file belongs to the caller's storage and row. |
| GET | `/api/media/inbox/trash` | user | Inbox file belongs to the caller's storage and row. |
| DELETE | `/api/media/inbox/{kind}/{filename}` | user | Inbox file belongs to the caller's storage and row. |
| GET | `/api/media/inbox/{kind}/{filename}` | user | Inbox file belongs to the caller's storage and row. |
| POST | `/api/media/inbox/{kind}/{filename}/assign` | user | Inbox file belongs to the caller's storage and row. |
| POST | `/api/media/inbox/{kind}/{filename}/restore` | user | Inbox file belongs to the caller's storage and row. |
| GET | `/api/media/provider-state` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/movie/create` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| DELETE | `/api/movie/delete/{movie_id}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/movie/get/{movie_id}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/movie/list` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/movie/metadata/search` | user | Lookup using provider data or the caller's configured credentials; no stored object is addressed. |
| GET | `/api/movie/trash` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/movie/trash/empty` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| PATCH | `/api/movie/update/{movie_id}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| DELETE | `/api/movie/{movie_id}/purge` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/movie/{movie_id}/recommended` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/movie/{movie_id}/relations` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/movie/{movie_id}/restore` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/preferences` | user | Own settings and provider credentials; secrets are never returned. |
| PATCH | `/api/preferences` | user | Own settings and provider credentials; secrets are never returned. |
| GET | `/api/rewatches` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| POST | `/api/rewatches` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| DELETE | `/api/rewatches/{rewatch_id}` | user | Entry filtered by `user_id == caller`; referenced media must be the caller's. |
| GET | `/api/stats/overview` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/stats/weekly-digest` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/tv/create` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| DELETE | `/api/tv/delete/{show_id}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/tv/get/{show_id}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/tv/list` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/tv/metadata/search` | user | Lookup using provider data or the caller's configured credentials; no stored object is addressed. |
| GET | `/api/tv/trash` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/tv/trash/empty` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| PATCH | `/api/tv/update/{show_id}` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| DELETE | `/api/tv/{show_id}/purge` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/tv/{show_id}/recommended` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/tv/{show_id}/refresh-airing` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| GET | `/api/tv/{show_id}/relations` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/tv/{show_id}/restore` | user | Rows queried with `user_id == caller`; any child ID must also belong to that parent. |
| POST | `/api/tv/{show_id}/seasons` | user | Show owned by caller; season/episode must belong to that show. |
| DELETE | `/api/tv/{show_id}/seasons/{season_id}` | user | Show owned by caller; season/episode must belong to that show. |
| PATCH | `/api/tv/{show_id}/seasons/{season_id}` | user | Show owned by caller; season/episode must belong to that show. |
| GET | `/api/tv/{show_id}/seasons/{season_id}/episodes` | user | Show owned by caller; season/episode must belong to that show. |
| PATCH | `/api/tv/{show_id}/seasons/{season_id}/episodes/bulk-watched` | user | Show owned by caller; season/episode must belong to that show. |
| PATCH | `/api/tv/{show_id}/seasons/{season_id}/episodes/{episode_id}` | user | Show owned by caller; season/episode must belong to that show. |

### Notifications

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| GET | `/api/notifications` | user | Rows filtered by `user_id == caller`. |
| GET | `/api/notifications/email-unsubscribe` | public | Signed single-use link token; query string redacted from access logs. |
| POST | `/api/notifications/email-unsubscribe` | public | Signed single-use link token; query string redacted from access logs. |
| GET | `/api/notifications/email-verify` | public | Signed single-use link token; query string redacted from access logs. |
| POST | `/api/notifications/email-verify` | public | Signed single-use link token; query string redacted from access logs. |
| GET | `/api/notifications/live/{token}` | public | Unguessable, expiring, revocable live-link token; public projection only; path redacted from access logs. |
| GET | `/api/notifications/live/{token}/state` | public | Unguessable, expiring, revocable live-link token; public projection only; path redacted from access logs. |
| GET | `/api/notifications/policy` | user | Rows filtered by `user_id == caller`. |
| POST | `/api/notifications/read-all` | user | Rows filtered by `user_id == caller`. |
| POST | `/api/notifications/regenerate` | admin | Administrator. |
| GET | `/api/notifications/stories/by-link/{token}` | user | Full private view only when the link's story belongs to the caller (404 otherwise); path redacted from access logs. |
| POST | `/api/notifications/stories/live-links/revoke-all` | user | Revokes only the caller's own live links. |
| GET | `/api/notifications/stories/{story_id}` | user | Story must belong to the caller (404 otherwise). |
| POST | `/api/notifications/stories/{story_id}/actions/{action_id}` | user | Story must belong to the caller (404 otherwise); the host rechecks plugin grants before running the action. |
| GET | `/api/notifications/stories/{story_id}/deliveries` | user | Story must belong to the caller (404 otherwise); delivery diagnostics for the caller's own destinations. |
| GET | `/api/notifications/stories/{story_id}/images/{index}` | user | Story must belong to the caller (404 otherwise); host-proxied image. |
| GET | `/api/notifications/stories/{story_id}/live-links` | user | Story must belong to the caller (404 otherwise). |
| POST | `/api/notifications/stories/{story_id}/live-links` | user | Story must belong to the caller (404 otherwise). |
| DELETE | `/api/notifications/stories/{story_id}/live-links/{link_id}` | user | Story and link must belong to the caller (404 otherwise). |
| POST | `/api/notifications/test` | admin | Administrator. |
| GET | `/api/notifications/unread-count` | user | Rows filtered by `user_id == caller`. |
| DELETE | `/api/notifications/{notification_id}` | user | Rows filtered by `user_id == caller`. |
| POST | `/api/notifications/{notification_id}/dismiss` | user | Rows filtered by `user_id == caller`. |
| POST | `/api/notifications/{notification_id}/read` | user | Rows filtered by `user_id == caller`. |
| POST | `/api/notifications/{notification_id}/unread` | user | Rows filtered by `user_id == caller`. |
| GET | `/api/settings/notification-diagnostics/deliveries` | user | Only deliveries of the caller's own notifications; redacted diagnostics. |
| POST | `/api/settings/notification-diagnostics/deliveries/{delivery_id}/retry` | user | Delivery must belong to one of the caller's notifications (404 otherwise); sensitive purposes are never re-sent. |
| POST | `/api/settings/notification-diagnostics/digest` | admin | Administrator; dry-run digest preview with fixtures. |
| GET | `/api/settings/metrics` | admin | Administrator; scrape status and, only for an application-generated token, its value. |
| GET | `/api/settings/notification-diagnostics/overview` | admin | Administrator; instance delivery health, redacted. |
| POST | `/api/settings/notification-diagnostics/preview` | admin | Administrator; dry-run render with fixtures. |
| POST | `/api/settings/notification-diagnostics/simulate` | admin | Administrator; dry-run routing simulation, nothing is queued. |
| POST | `/api/settings/notification-diagnostics/test-send` | admin | Administrator; test send to the administrator's own destinations. |
| GET | `/api/settings/notification-providers` | user | Rows filtered by `user_id == caller`. |
| GET | `/api/settings/notification-providers/browser-configuration` | user | Rows filtered by `user_id == caller`. |
| POST | `/api/settings/notification-providers/browser-configuration/key` | admin | Instance notification configuration; administrator. |
| POST | `/api/settings/notification-providers/browser-destinations` | user | Rows filtered by `user_id == caller`. |
| DELETE | `/api/settings/notification-providers/browser-destinations/{destination_id}` | user | Destination must belong to the caller (404 otherwise). |
| PATCH | `/api/settings/notification-providers/browser-destinations/{destination_id}` | user | Destination must belong to the caller (404 otherwise). |
| GET | `/api/settings/notification-providers/browser-destinations/{destination_id}/status` | user | Destination must belong to the caller (404 otherwise). |
| GET | `/api/settings/notification-providers/destinations` | user | Rows filtered by `user_id == caller`. |
| POST | `/api/settings/notification-providers/destinations/{destination_id}/test` | user | Destination must belong to the caller (404 otherwise). |
| PATCH | `/api/settings/notification-providers/destinations/{destination_id}/url` | user | Destination must belong to the caller (404 otherwise). |
| POST | `/api/settings/notification-providers/email-destinations` | user | Rows filtered by `user_id == caller`. |
| DELETE | `/api/settings/notification-providers/email-destinations/{destination_id}` | user | Destination must belong to the caller (404 otherwise). |
| PATCH | `/api/settings/notification-providers/email-destinations/{destination_id}` | user | Destination must belong to the caller (404 otherwise). |
| POST | `/api/settings/notification-providers/email-destinations/{destination_id}/verification` | user | Destination must belong to the caller (404 otherwise). |
| POST | `/api/settings/notification-providers/email-destinations/{destination_id}/verification/confirm` | user | Destination must belong to the caller (404 otherwise). |
| POST | `/api/settings/notification-providers/email-destinations/{destination_id}/verification/revoke` | user | Destination must belong to the caller (404 otherwise). |
| POST | `/api/settings/notification-providers/plugin-destinations` | user | Rows filtered by `user_id == caller`. |
| DELETE | `/api/settings/notification-providers/plugin-destinations/{destination_id}` | user | Destination must belong to the caller (404 otherwise). |
| PUT | `/api/settings/notification-providers/plugin-destinations/{destination_id}` | user | Destination must belong to the caller (404 otherwise). |
| PUT | `/api/settings/notification-providers/plugin-destinations/{destination_id}/recovery` | user | Destination must belong to the caller (404 otherwise). |
| POST | `/api/settings/notification-providers/plugin-destinations/{destination_id}/verification` | user | Destination must belong to the caller (404 otherwise). |
| POST | `/api/settings/notification-providers/plugin-destinations/{destination_id}/verification/confirm` | user | Destination must belong to the caller (404 otherwise). |
| POST | `/api/settings/notification-providers/plugin-destinations/{destination_id}/verification/revoke` | user | Destination must belong to the caller (404 otherwise). |
| PUT | `/api/settings/notification-providers/plugin-server-configuration/{provider_id}` | admin | Instance notification configuration; administrator. |
| POST | `/api/settings/notification-providers/smtp/test` | admin | Instance notification configuration; administrator. |
| POST | `/api/settings/notification-providers/webhook-destinations` | user | Rows filtered by `user_id == caller`. |
| DELETE | `/api/settings/notification-providers/webhook-destinations/{destination_id}` | user | Destination must belong to the caller (404 otherwise). |
| PATCH | `/api/settings/notification-providers/webhook-destinations/{destination_id}` | user | Destination must belong to the caller (404 otherwise). |
| PUT | `/api/settings/notification-providers/{provider_id}` | user | Rows filtered by `user_id == caller`. |
| GET | `/api/settings/notification-timing/preview` | user | `destination_id` query must name the caller's own destination (404 otherwise); read-only preview, nothing is queued. |

### Sessions

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| GET | `/api/sessions/admin` | admin | Administrator session review. |
| DELETE | `/api/sessions/admin/all` | admin | Administrator session review. |
| POST | `/api/sessions/admin/geoip` | admin | Administrator session review. |
| GET | `/api/sessions/admin/geoip/status` | admin | Administrator session review. |
| DELETE | `/api/sessions/admin/user/{user_id}` | admin | Administrator session review. |
| DELETE | `/api/sessions/admin/{session_id}` | admin | Administrator session review. |
| GET | `/api/sessions/me` | user | Own sessions only; another user's session ID is 404. |
| DELETE | `/api/sessions/me/all` | user | Own sessions only; another user's session ID is 404. |
| DELETE | `/api/sessions/me/{session_id}` | user | Own sessions only; another user's session ID is 404. |

### Settings, jobs and administration

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| GET | `/api/metadata/automatic-search` | user | Read: policy summary. Write: instance policy; administrator. |
| PUT | `/api/metadata/automatic-search` | admin | Read: policy summary. Write: instance policy; administrator. |
| GET | `/api/metadata/providers` | user | System-scope configuration requires an administrator; user scope is the caller's own. |
| PUT | `/api/metadata/providers/{provider_id}/configuration` | user | System-scope configuration requires an administrator; user scope is the caller's own. |
| POST | `/api/metadata/selection` | user | Caller's own search preferences and sessions. |
| POST | `/api/metadata/sessions` | user | In-memory search session bound to the creating user (404 for others). |
| DELETE | `/api/metadata/sessions/{session_id}` | user | In-memory search session bound to the creating user (404 for others). |
| GET | `/api/metadata/sessions/{session_id}` | user | In-memory search session bound to the creating user (404 for others). |
| POST | `/api/metadata/sessions/{session_id}/artwork` | user | In-memory search session bound to the creating user (404 for others). |
| GET | `/api/metadata/sessions/{session_id}/events` | user | In-memory search session bound to the creating user (404 for others). |
| POST | `/api/metadata/sessions/{session_id}/selection` | user | In-memory search session bound to the creating user (404 for others). |
| DELETE | `/api/settings/app-integrations` | admin | Instance configuration; administrator. |
| GET | `/api/settings/app-integrations` | admin | Instance configuration; administrator. |
| PUT | `/api/settings/app-integrations` | admin | Instance configuration; administrator. |
| DELETE | `/api/settings/app-integrations/providers` | admin | Instance configuration; administrator. |
| GET | `/api/settings/app-integrations/providers` | admin | Instance configuration; administrator. |
| PUT | `/api/settings/app-integrations/providers` | admin | Instance configuration; administrator. |
| GET | `/api/settings/appearance` | user | Own settings and provider credentials; secrets are never returned. |
| PUT | `/api/settings/appearance` | user | Own settings and provider credentials; secrets are never returned. |
| DELETE | `/api/settings/appearance/badge-image` | user | Own settings and provider credentials; secrets are never returned. |
| GET | `/api/settings/appearance/badge-image` | user | Own settings and provider credentials; secrets are never returned. |
| POST | `/api/settings/appearance/badge-image` | user | Own settings and provider credentials; secrets are never returned. |
| GET | `/api/settings/deployment` | admin | Instance configuration; administrator. |
| PUT | `/api/settings/deployment` | admin | Instance configuration; administrator. |
| GET | `/api/settings/jobs` | admin | Instance schedule; administrator. |
| PUT | `/api/settings/jobs/{job_id}` | admin | Instance schedule; administrator. |
| POST | `/api/settings/jobs/{job_id}/run` | admin | Instance schedule; administrator. |
| POST | `/api/settings/jobs/{job_id}/{control}` | admin | Instance schedule; administrator. |
| GET | `/api/settings/provider-credentials` | user | Own settings and provider credentials; secrets are never returned. |
| DELETE | `/api/settings/provider-credentials/{provider}` | user | Own settings and provider credentials; secrets are never returned. |
| PUT | `/api/settings/provider-credentials/{provider}` | user | Own settings and provider credentials; secrets are never returned. |
| POST | `/api/settings/refresh-media-metadata` | admin | Instance configuration; administrator. |
| GET | `/api/settings/refresh-media-progress` | admin | Instance configuration; administrator. |
| GET | `/api/settings/scan` | user | Own settings and provider credentials; secrets are never returned. |
| PUT | `/api/settings/scan` | user | Own settings and provider credentials; secrets are never returned. |
| DELETE | `/api/settings/steam` | user | Own settings and provider credentials; secrets are never returned. |
| GET | `/api/settings/steam` | user | Own settings and provider credentials; secrets are never returned. |
| POST | `/api/settings/steam/login/account` | user | Own settings and provider credentials; secrets are never returned. |
| POST | `/api/settings/steam/login/cancel` | user | Own settings and provider credentials; secrets are never returned. |
| POST | `/api/settings/steam/login/guard` | user | Own settings and provider credentials; secrets are never returned. |
| POST | `/api/settings/steam/openid/begin` | user | Own settings; starts a single-use redirect state bound to the caller's session. |
| GET | `/api/settings/steam/openid/callback` | user | Own settings; the state must match the caller's session and Steam's assertion is verified before the identity is linked. |
| POST | `/api/settings/steam/login/poll` | user | Own settings and provider credentials; secrets are never returned. |
| POST | `/api/settings/steam/login/qr` | user | Own settings and provider credentials; secrets are never returned. |
| GET | `/api/settings/system-info` | admin | Instance configuration; administrator. |
| PUT | `/api/settings/upload-limit` | admin | Instance configuration; administrator. |
| GET | `/api/settings/upload-limit-sources` | user | Where each upload cap comes from and whether the environment locks it; no stored object is addressed and no secret is returned. |
| GET | `/api/settings/upload-limits` | user | Lookup using provider data or the caller's configured credentials; no stored object is addressed. |
| GET | `/api/settings/user-jobs` | user | Per-user job preferences; the job must be one the caller can see. |
| PUT | `/api/settings/user-jobs/{job_id}` | user | Per-user job preferences; the job must be one the caller can see. |
| POST | `/api/settings/user-jobs/{job_id}/run` | user | Per-user job preferences; the job must be one the caller can see. |
| POST | `/api/settings/user-jobs/{job_id}/{control}` | user | Per-user job preferences; the job must be one the caller can see. |
| PUT | `/api/themes/default` | admin | Instance themes; administrator. |
| POST | `/api/themes/install` | admin | Instance themes; administrator. |
| POST | `/api/themes/install/preview` | admin | Instance themes; administrator. |
| GET | `/api/themes/manage` | admin | Instance themes; administrator. |
| DELETE | `/api/themes/{theme_id}` | admin | Instance themes; administrator. |
| PATCH | `/api/themes/{theme_id}` | admin | Instance themes; administrator. |

### Plugin platform

| Method | Path | Authority | Ownership and effect |
| --- | --- | --- | --- |
| GET | `/api/plugin-permissions/clients` | user | Device credentials bound to, and listed/revoked only for, the caller. |
| POST | `/api/plugin-permissions/clients` | user | Device credentials bound to, and listed/revoked only for, the caller. |
| POST | `/api/plugin-permissions/clients/{client_id}/revoke` | user | Device credentials bound to, and listed/revoked only for, the caller. |
| GET | `/api/plugin-permissions/grants` | plugin-admin | Permission review/grants; administrator or scoped management token. |
| POST | `/api/plugin-permissions/grants/{grant_id}/revoke` | plugin-admin | Permission review/grants; administrator or scoped management token. |
| GET | `/api/plugin-permissions/requests` | plugin-admin | GET: administrator. POST: own-scope proposal; installation-wide proposals are administrator-only. |
| POST | `/api/plugin-permissions/requests` | user | GET: administrator. POST: own-scope proposal; installation-wide proposals are administrator-only. |
| POST | `/api/plugin-permissions/requests/{request_id}/approve` | plugin-admin | Permission review/grants; administrator or scoped management token. |
| POST | `/api/plugin-permissions/requests/{request_id}/deny` | plugin-admin | Permission review/grants; administrator or scoped management token. |
| GET | `/api/plugins` | plugin-reader | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| GET | `/api/plugins/catalog` | plugin-reader | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| GET | `/api/plugins/catalogues` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/catalogues` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| DELETE | `/api/plugins/catalogues/{catalogue_id}` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| PATCH | `/api/plugins/catalogues/{catalogue_id}` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/install` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/install/preview` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/install/preview-url` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/install/url` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/management/tokens` | admin | Administrator mints or revokes dedicated `utpm_` management credentials. |
| DELETE | `/api/plugins/management/tokens/{token_id}` | admin | Administrator mints or revokes dedicated `utpm_` management credentials. |
| GET | `/api/plugins/manager-settings` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| PUT | `/api/plugins/manager-settings` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/runtime/gateway` | plugin-runtime | Runtime credential; plugin calls are limited to declared, granted capabilities. |
| GET | `/api/plugins/runtime/health` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/updates/check` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| DELETE | `/api/plugins/{plugin_id}` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/actions/{action_id}` | user | Caller's own plugin state; execution still needs the installation's granted capabilities. |
| PUT | `/api/plugins/{plugin_id}/auto-update` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| GET | `/api/plugins/{plugin_id}/capabilities/documents/{document_id}/download` | user | Caller's own plugin state; execution still needs the installation's granted capabilities. |
| POST | `/api/plugins/{plugin_id}/capabilities/sessions/geoip` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| GET | `/api/plugins/{plugin_id}/changelog` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| GET | `/api/plugins/{plugin_id}/details` | plugin-reader | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/disable` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/enable` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| GET | `/api/plugins/{plugin_id}/frontend/{asset_path:path}` | user | Caller's own plugin state; execution still needs the installation's granted capabilities. |
| DELETE | `/api/plugins/{plugin_id}/history/{history_id}` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| GET | `/api/plugins/{plugin_id}/logs` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| GET | `/api/plugins/{plugin_id}/native-frontend/{asset_path:path}` | user | Caller's own plugin state; execution still needs the installation's granted capabilities. |
| POST | `/api/plugins/{plugin_id}/permissions/grant` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/permissions/preview` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/permissions/revoke` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/reinstall` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/retry` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/rollback` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| PUT | `/api/plugins/{plugin_id}/secrets/{key}` | user | Caller's own plugin state; execution still needs the installation's granted capabilities. |
| GET | `/api/plugins/{plugin_id}/settings` | user | Caller's own plugin state; execution still needs the installation's granted capabilities. |
| PUT | `/api/plugins/{plugin_id}/settings` | user | Caller's own plugin state; execution still needs the installation's granted capabilities. |
| POST | `/api/plugins/{plugin_id}/start` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/stop` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| GET | `/api/plugins/{plugin_id}/ui` | user | Caller's own plugin state; execution still needs the installation's granted capabilities. |
| PUT | `/api/plugins/{plugin_id}/update` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| PUT | `/api/plugins/{plugin_id}/update/preview` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/update/preview-url` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/update/staged` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/update/staged/preview` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| POST | `/api/plugins/{plugin_id}/update/url` | plugin-admin | Plugin lifecycle; administrator (or a management token scoped to the operation). |
| DELETE | `/api/plugins/{plugin_id}/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
| GET | `/api/plugins/{plugin_id}/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
| PATCH | `/api/plugins/{plugin_id}/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
| POST | `/api/plugins/{plugin_id}/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
| PUT | `/api/plugins/{plugin_id}/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
| DELETE | `/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
| GET | `/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
| PATCH | `/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
| POST | `/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
| PUT | `/{route_path:path}` | plugin-route | Plugin-defined route; the declared route authentication and grants are enforced before dispatch. |
