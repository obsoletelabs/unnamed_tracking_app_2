# User-approved integration access

Instance owners enable **Administration → Integration access** and approve an
integration's exact HTTPS origin. This allows it to request access, but grants no
user data. Each user signs in to their own instance to review the integration,
capabilities and expiry. Write permissions start unchecked.

Hosted library connections request only identity and game reads by default.
Optional progress features request achievement reads and tracking/achievement
writes separately, with the writes still unchecked on the source consent screen.

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

Resource IDs never permit cross-user reads or writes. These credentials are
rejected on ordinary app, API-key management, authentication, plugin-management
and administration routes. Existing `utk_` API keys retain their legacy behavior;
they are not accepted as delegated credentials. Browser sessions are required
to manage consent and grants.

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
