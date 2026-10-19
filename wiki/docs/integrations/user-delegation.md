# User-approved integration access

Instance owners enable **Administration → Integration access** and approve an
integration's exact HTTPS origin. This allows it to request access, but grants no
user data. Each user signs in to their own instance to review the integration,
capabilities and expiry. Write permissions start unchecked.

Hosted library connections request only identity and game reads by default.
Optional progress features request achievement reads and tracking/achievement
writes separately, with the writes still unchecked on the source consent screen.

For an Activity host, save its public HTTPS URL under **Settings → Administration
→ Application → Shared public app URL**. Setup can save the same value. No
environment variable is required; an optional `PUBLIC_APP_URL` override takes
precedence. Source instances must approve that exact host origin, including when
the source and Activity are on the same instance. A connection error asking for
the Activity host's public app URL means its administrator needs to save this
setting. Use the host's own domain, not a `discordsays.com` proxy address.

Owners can optionally enable **Allow consent requests from any HTTPS integration
origin**. An integration may then register its requester identity using
`POST /api/delegation/v1/clients/register` with its name and exact HTTPS origin.
This does not issue a credential or approve access. Individually disabled origins
remain blocked; registration cannot rename or re-enable them. Registration is
bounded to 1,000 clients per instance. Leave this option off to approve each
requester yourself.

Users can choose **Until revoked** for ongoing integration access. Timed access
is selected by default and respects the owner's maximum timed duration. Ongoing
grants have `expires_at: null`; revocation, disabled clients, disabled integrations
and account deactivation still block every subsequent request. Guest retention
and Activity sessions remain bounded independently of the user's access grant.

**Account → Connected apps** shows issued grants. Users can remove capabilities
or revoke a grant. New capabilities require a new consent flow. Disabling the
integration origin, disabling integrations, deactivating an account, revoking a
grant or reaching its expiry blocks subsequent requests.

When supplied by the integration, the consent screen and Connected apps show the
linked external username and stable account ID. This label is reported by the
requester; check that it is the account you intended to connect. It never proves
ownership or merges accounts. Older remote grants may require reconnecting to
show a label; local broker grants use their exact consent request ID.

Administrators can set guest/room retention from 1 to 8760 hours (365 days).
This is separate from timed or Until revoked account grants. A Discord guest is
an externally signed-in participant without a connected tracking library, not a
new application user. Plugins may expose an administrator page for saved external
participants, guarded by the separate `integrations.public_app.admin` permission.
Unlink/purge there forgets local integration data; source-side grants remain
revocable in the source instance's Connected apps.

The integration retains an opaque credential, rather than a password, app
session or unrestricted API key. Credentials are restricted to the consenting
user, and never provide administration access, even for an administrator.

## Desktop apps

First-party desktop apps the host knows by name (today the
[Playnite extension](playnite.md#authentication)) use the same consent and exchange without an
operator-approved web origin. They start with
`POST /api/delegation/v1/desktop/{app}/requests`, sending `scopes`, `code_challenge`, a
`device_name` and a stable `device_id`; the device is shown on the consent page and in Connected
apps as display context, like `external_account`. `GET /instance` lists them in `desktop_apps`.

**Administration → Integration access → Let people connect desktop apps such as Playnite**
(`allow_desktop_apps`) governs them, on by default because each user could already create a
full-access API key; it is independent of **Enable user-delegated web integrations**. The
desktop client appears in the client list as *Desktop app* and can be disabled like an origin.
On the consent page a desktop app's requested capabilities start selected and its access defaults
to Until revoked; web integrations keep writes unchecked and a timed default.

An app signs out with `DELETE /api/delegation/v1/credential`, which revokes its own grant.

## Protocol v1.0

All paths below are under `/api/delegation/v1`. Responses are not cacheable.

1. Read `GET /instance` for the stable instance ID, enabled policy, approved
   integration clients and supported capabilities.
2. Create `POST /requests` with `client_id`, `scopes` and `code_challenge`, the
   hexadecimal SHA-256 of a random 43–128 character verifier held by the backend.
3. Open the returned `verification_path` in the participant's normal browser.
   Signed-in users approve a subset and an expiry, or deny the request. Send
   `expires_in_hours: null` in the decision to explicitly approve Until revoked;
   omitting it defaults to 24 hours. Existing grants keep their original expiry.
4. Poll `POST /requests/{id}/exchange` using `{"verifier": "..."}` no faster
   than the returned interval. Approval can be exchanged once. The request
   expires after ten minutes. Keep credentials and verifiers out of URLs.
5. Use `Authorization: Bearer utd_...` on the scoped resource paths.
   `POST /credential/rotate` replaces the credential without increasing access
   or extending the expiry. A lost rotation response requires fresh consent.

| Capability | Paths and limits |
| --- | --- |
| `profile.read` | `GET /profile`: own ID and display name |
| `games.read` | `GET /games`, `/games/{id}`, `/games/{id}/artwork/{kind}`: own library and artwork |
| `achievements.read` | `GET /games/{id}/achievements`: own stored provider data and local reports |
| `history.read` | `GET /games/{id}/history`: own game's field history |
| `lists.read` | `GET /lists`, `/lists/{id}`: own lists and contents |
| `media.read` | `GET /media?kind=movie\|tv\|anime`: own tracked media |
| `tracking.write` | `PATCH /games/{id}/tracking`: status, favorite, playtime, completion and ratings only |
| `compatibility.write` | `PATCH /games/{id}/play-compatibility`: replace/clear your own multiplayer preset; separate from tracking writes |
| `achievements.write` | `POST /games/{id}/achievement-reports`, `DELETE /games/{id}/achievement-reports/{report_id}`: local self reports only |
| `library.sync` | The ordinary `GET /api/game/list`, `POST /api/game/create`, `PATCH /api/game/update/{id}`, `POST /api/game/{id}/assets/{kind}` and `/api/playnite/*` routes, as the consenting user (own games only) |
| `saves.read` | `GET /api/game/{id}/archives/{kind}` and archive version downloads |
| `saves.write` | `POST /api/game/{id}/archives/{kind}`, `POST .../archives/{archive_id}/versions`, `PATCH .../archives/{archive_id}` |
| `content.write` | `POST /api/game/{id}/screenshots` (images, videos, audio) and `POST /api/game/{id}/files/doc`; `files/modpack` is refused for app credentials |

The last four reach ordinary application routes marked `user-or-app` in the
[API access matrix](../development/api-access-matrix.md); in code, `@app_grant(scope)` on the
route lets `get_current_user` accept a `utd_` credential carrying that scope as the consenting
user. Every unmarked route still refuses `utd_` credentials. Resource IDs never permit
cross-user reads or writes. The
[API access matrix](../development/api-access-matrix.md) lists every route and the
credential class it accepts. These credentials are
rejected on ordinary app, API-key management, authentication, plugin-management
and administration routes. Existing `utk_` API keys retain their legacy behavior;
they are not accepted as delegated credentials. Browser sessions are required
to manage consent and grants.

The delegated library's additive `identity` block (`group_id`, `is_main`, `main_title`)
describes how this user groups copies in their library. It is presentation metadata.
Each returned copy retains its own source, ownership/access state, platform, edition,
and multiplayer constraints. Consumers must check those copy-level claims before
offering play together; sharing a group or main title does not prove compatible
editions, cross-platform support, or current access. Do not collapse copies in a way
that lets an offline source hide another accessible copy.

## Reference client and identity-provider extension points

The companion plugins repository ships a packaged, non-Discord reference consumer,
`reference-clients/integration-access`, that uses only this versioned contract: it
discovers the instance, registers or uses an approved origin, requests a read-only
subset, optionally requests writes separately, exchanges once, rotates and handles
reduction, revocation and disabled clients. Its `tools/check_integration_access.py`
run exercises denial, timed and Until revoked grants, origin policy, cross-user IDs
and legacy API-key compatibility against a real instance.

Identity providers (Discord and others) live in their own adapters outside the host.
An adapter authenticates its own users, then passes `external_account`
(`provider`, `subject`, `username`) in the access request as display context. The
host stores and shows it but never treats it as ownership proof, and the adapter
never receives host infrastructure. Origin approval or the any-HTTPS-origin policy
only lets a client ask; a user must still approve each capability.

## Data provenance

A wishlist entry is not ownership. Library ownership is reported as
`instance_library`; it does not imply a fresh provider check. Unknown provider
check times stay null. Purchase history is not a current offer. Offer data is
currently unavailable, so integrations cannot infer a current price or discount.

Provider achievement rows retain their imported state. A self-reported override
is stored separately by game, provider and provider achievement ID, and survives
provider resynchronization. It is labeled `self_reported_override`, with
`verified: false`. Manual reports are labeled `manual`. Neither is provider
verification. Provider names and descriptions cannot be changed by an override.

## Operations

Apply migrations through `e8c64dd34f5c` through the normal deployment migration process.
The policy starts disabled. Database backups include grant hashes and local
reports. Audit records contain event metadata, never credentials or library
contents. Integration operators should delete their copies of credentials when
participants disconnect.

The new migrations use the existing adoption helpers so replay on an already
migrated database preserves clients, grants and game data. This repairs the
unreleased integration migrations' replay behavior without changing their schema
or introducing a competing migration head. Downgrading beyond ongoing-access
support expires ongoing grants, and downgrading beyond legacy support removes
legacy connections because the older schema cannot represent them safely.

An Activity operator may separately enable unsafe legacy API-key connections to
older instances. Participants must acknowledge the broad key. The broker uses
only fixed identity and library GET paths; it cannot perform legacy tracking or
achievement writes. The key remains encrypted at the operator and is never sent
to plugin code. Disconnecting removes the broker copy; revoke the key at the old
source instance to invalidate it there.

See [public-app hosting](../development/plugin-public-apps.md) for external
principals, host-held source connections and the scoped route enforcement matrix.
