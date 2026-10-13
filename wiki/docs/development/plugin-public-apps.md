# Public apps and identity providers

Plugin API **1.1.12** adds public applications whose participants do not need a
host account. The host owns sessions, external principals, consent, encrypted
source credentials, image transport and capability enforcement. The plugin owns
its application logic and UI. Discord-specific OAuth and membership checks live
in the independent Discord bot plugin; the host contains no Discord bot logic.

## Declarations and permissions

A public-app manifest declares `public_app` with an `entry` beneath `frontend/`,
a declared `action_id`, and an `identity_provider_plugin_id` that is also a
dependency. An identity-provider manifest declares `identity_provider` with
`config_action_id`, `authenticate_action_id` and `revalidate_action_id`; all must
reference declared actions.

Both require API 1.1.12 and their exact, explicit, installation-wide permissions:
`integrations.public_app` or `integrations.identity_provider`. Neither is implied
by `api.full`. The host requires executable current installations and live grants.
Disabling or reinstalling either package invalidates the old authority.

The provider returns a verified subject, room and display name. Optional HTTPS
avatar data is treated as a public image URL, never as user authority. The host
creates a separate external actor and hashed `utp_` session; it does not create or
claim a `User`. The provider's configured server identity is used only by its
explicit adapter actions. The Activity action receives the external actor, never
that administrator's application identity.

The private runtime bridge injects reserved context after rejecting client-supplied
reserved fields. External actors can authorize only their own installation's
storage/public-app operations. They cannot call ordinary account gateway methods.
Verified public work has a separate 4 MiB input/result bound; ordinary actions keep
their 64 KiB limits. Browser action values remain limited to 48 KiB.

## Sessions, sources and public media

Public sessions last 30 minutes and slide on authenticated requests. Membership is
rechecked after 60 seconds; room contributions recheck source credentials at most
five seconds apart and before roll/finalize. Library refresh is explicit. A source
must still authorize every remote mutation on its own server. Expired/revoked
sources lose their cached claims; offline sources cannot substantiate access.

Source connections are private to the verified actor. Their credentials/verifiers
are encrypted in host storage and are absent from plugin context and browser
responses. The broker supports at most five sources and 2,000 games per source.
Ongoing account connections survive guest retention while their credentials are
live; room/session retention remains finite. Disconnect removes the local broker
copy. Source-side Connected apps revocation invalidates the remote grant too.

The bounded HTTPS transport validates all resolved addresses as public and pins a
validated address while preserving TLS hostname checks. It follows no redirects
and sends no ambient cookies. Legacy mode permits only the fixed identity/library
GET operations and never uses broad keys for writes. Public raster images have a
2 MiB limit; PNG/JPEG/WebP are accepted by their signatures, and SVG/HTML are
rejected. Participant avatars require a live same-room session in the same
installation. Private image responses use `Cache-Control: no-store`.

Public catalogue lookups reuse the hardcoded Steam provider. A title lookup needs
one normalized exact candidate; ambiguous editions remain unmatched. Metadata and
artwork never prove ownership, player limits, current offers or provider-verified
achievements. Users can save their own group-play details in the game editor.

## Scoped delegation enforcement matrix

All paths in this table are under `/api/delegation/v1`. `utd_` credentials are
accepted only by explicitly delegated resource operations. Every resource call
rechecks the grant, active account and enabled client/policy. Supplying a different
object ID does not replace the authenticated owner.

| Method and path | Authority / scope | Ownership and allowed effect |
| --- | --- | --- |
| GET `/instance` | Public discovery | Minimized policy, stable instance ID and enabled requester identities; no account data |
| GET `/policy` | Application administrator | Read instance integration policy |
| PUT `/policy` | Application administrator | Change requester/guest/legacy policy; no user grant created |
| PUT `/clients` | Application administrator | Approve/disable exact HTTPS requester origin |
| POST `/clients/register` | Enabled open-origin policy | Bounded registration only; disabled client remains disabled |
| POST `/requests` | Enabled registered requester | Supported scopes and verifier challenge; no user selected yet |
| GET `/requests/{id}` | Browser session | Review integration identity, scopes and expiry |
| POST `/requests/{id}/decision` | Browser session | Approve a requested subset for this signed-in user, or deny; one decision |
| POST `/requests/{id}/exchange` | One-use request + verifier | Exchange only the approved grant; no caller-selected user |
| GET `/grants` | Browser session | Only this user's grant list |
| PATCH `/grants/{id}` | Browser session | Owned grant; reduction only |
| DELETE `/grants/{id}` | Browser session | Owned grant; revoke |
| GET `/credential` | Live delegated grant | Own scopes and expiry only |
| POST `/credential/rotate` | Live delegated grant | Replace only the presented credential; scopes/expiry unchanged |
| GET `/profile` | `profile.read` | Own ID/display name; no email, secrets or administrator fields |
| GET `/games` | `games.read` | Own nondeleted games; paginated 1–200 |
| GET `/games/{id}` | `games.read` | Owned nondeleted game projection |
| GET `/games/{id}/artwork/{kind}` | `games.read` | Owned game and known asset kind; no arbitrary path |
| GET `/games/{id}/achievements` | `achievements.read` | Owned game; provider state and separate local reports |
| GET `/games/{id}/history` | `history.read` | Owned game's field history |
| GET `/lists` | `lists.read` | Own lists |
| GET `/lists/{id}` | `lists.read` | Owned list and contents |
| GET `/media` | `media.read` | Own nondeleted movie/TV/anime projections; paginated |
| PATCH `/games/{id}/tracking` | `tracking.write` | Owned game; status/favorite/playtime/completion/ratings allowlist |
| POST `/games/{id}/achievement-reports` | `achievements.write` | Owned game; manual report or local provider-ID override; provider evidence unchanged |
| DELETE `/games/{id}/achievement-reports/{report_id}` | `achievements.write` | Report must belong to this owned game |

Ordinary application routes use `get_current_user` (and `get_current_admin` where
required), which rejects `utd_` and `utp_` before evaluating browser cookies or
route-level ownership. An administrator's delegated grant is therefore still
unable to manage users, API keys, sessions, plugins, notifications, metadata
providers or instance configuration. Plugin-management/runtime bridges use their
separate credential contracts. Public sign-in/registration/invitation endpoints
retain their own bootstrap policy and confer no authority from an integration
credential. Guest account claiming is not provided by the public-app protocol.

Legacy `utk_` creation generates a random secret and stores its SHA-256 hash.
Validation joins the active user and nonrevoked key; there is no effective scope
list to rely on. Existing API keys retain that user's existing route authority,
including administrator access only when that user is an administrator. They
remain a separate compatibility path and are never silently narrowed or upgraded
into delegated credentials.

See [user-approved access](../integrations/user-delegation.md) for the consent,
rotation and revocation protocol. Regression coverage in `test_delegation.py`,
`test_public_apps.py`, `test_public_catalogue.py`, the authentication tests and
runtime limit tests covers manipulated owners/IDs, rejected ordinary/admin paths,
revocation, provider-local reports, source isolation, malformed avatars and image
transport. The matrix documents the integration boundary; it is not a claim of an
unrelated whole-application penetration test.

## Public-app HTTP routes

Under `/api/public-apps/{plugin_id}`, `/config` and `/app/...` expose only the
enabled app's public configuration and packaged frontend. `POST /sessions` passes
bounded proof to the declared provider; `DELETE /sessions` invalidates the current
session. `/action`, `/catalogue`, `/catalogue/{id}/artwork`, participant avatars and
all `/sources` operations require an authenticated external session and current
installation grants. Source IDs are checked against that actor before consent
polling, refresh, disconnect, own-game artwork, tracking or achievement operations.
Remote source ownership and write scopes are enforced again by delegation routes.

`POST /sources` accepts an optional unique `scopes` list. It defaults to
`profile.read` and `games.read`, which the library broker requires. Additional
capabilities must be requested deliberately and remain subject to source-side
consent; requesting them never grants them. No unused media/list/history access
is added to a default library connection.

Frontend responses include a Discord-compatible frame-ancestor policy. A reverse
proxy must not add `X-Frame-Options: SAMEORIGIN` to the public Activity asset route.
Keep the ordinary application's frame protections. Set the deployment's public
HTTPS URL so source consent names the actual operator origin rather than the
Discord proxy domain.
