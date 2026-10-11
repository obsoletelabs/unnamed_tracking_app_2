# Host-mediated authentication plugins

Plugin API 1.1.7 adds optional password recovery; 1.1.8 adds administrator invitations.
Both use the existing
plugin gateway, permission review, installation identity and runtime lifecycle.
The wire major remains `v1`; earlier supported plugins continue working.

## Permission and registration

`auth.password_reset` is a separate critical permission, not implied by `api.full`,
users, notification sources or providers. It requires an explicit global grant:
public recovery cannot depend on an unauthenticated caller's personal grant.

Administrator gateway actions:

- `auth_flows.password_reset.register`: `{ "mode": "password_reset", "name": "Password Reset", "token_lifetime_seconds": 1200 }`.
- `auth_flows.password_reset.restore`: the same definition; resumes only an already-active matching installation and definition, and otherwise returns `registered: false`.
- `auth_flows.password_reset.unregister`: `{}`.

Lifetime is 600–3600 seconds. Only one reset contribution can occupy the public
entry point. Configuration changes and withdrawal invalidate outstanding tokens.
Registration may occur during verified worker startup, but public status, request,
redemption and delivery require the current installation to be enabled, compatible
and running with the exact global grant. Grant withdrawal, disabling and uninstall
consume old credentials permanently and suppress unsent work. Reinstall requires
explicit administrator activation and cannot redeem old tokens.

Plugins provide installable activation/configuration controls; the host owns
credential generation, password policy/hash, atomic redemption and account mutation.
There is no anonymous general-purpose plugin gateway and no destination/address
enumeration API. The maintained consumer is `official.password-reset` in the
[companion repository](https://github.com/obsoletelabs/unnamed_tracking_app_plugins/tree/main/official/password-reset).

## Notification and token lifecycle

The host reserves `auth.password_reset`. Normal plugin notification sources cannot
register this type. The controller creates a generic durable notification with
SECURE required trust and recovery purpose. Preferences, administrative restrictions,
current proof, external context and explicit recovery opt-in determine deliveries.
Plugin transports additionally need declared recovery support and their separate
high-risk content grant. In-app/browser destinations receive no recovery secret.

The token is hashed for redemption and encrypted separately for eligible delivery.
Canonical content and content-free lifecycle records contain no bearer secret.
The core resolves the link at final permitted transport handoff. Destination and
personal notification URL preferences precede the configured public application
URL and the user's last authenticated app URL; submitted request Origin is ignored.
Fragments keep bearer material out of ordinary HTTP GET/access logs, and the reset
page removes the fragment from the displayed address.

Opening a link never consumes it. A host POST locks the account before the token,
validates the current account/password/email/installation/configuration, and consumes
the token once while changing the password. All browser sessions and other pending
reset tokens are revoked; **API keys remain active**. No automatic sign-in occurs.
SSO-linked and inactive accounts cannot use local recovery. Unknown/ineligible
requests receive the same acknowledgement. Atomic per-hour caller/identifier limits
store keyed digests rather than submitted identifiers. Expiry clears encrypted
material; expired token records are removed after one day.

## Public host endpoints

- `GET /api/auth/password-reset/status` returns availability only.
- `POST /api/auth/password-reset/request` accepts `identifier`.
- `POST /api/auth/password-reset/complete` accepts token `id`, `token`, and `password`.

The schema is exported by `tools/export_auth_contract.py`; companion contract checks
compare its snapshot and actual packaged consumers against the paired host.

## Administrator email invitations (API 1.1.8)

`auth.user_invites` is a separate critical grant. An administrator explicitly
activates one installed contribution through `auth_flows.user_invites.register`.
Its narrow `status`, `issue`, `list`, `revoke`, `restore`, and `unregister` methods
manage only that installation's invitations. Listing exposes pending invite email
addresses and delivery metadata to the authorized administrator, never bearer tokens,
passwords, or SMTP credentials. `api.full` does not substitute for this grant.

The maintained consumer is `official.user-invites` in the companion repository.
The plugin supplies administrator controls; the host owns invitation credentials,
expiry, account policy, delivery routing, and atomic acceptance. Link lifetime is
one hour to seven days, with a one-day default. Issuance is limited to 100 per
administrator and five per email per day, with a bounded pending-recipient store.

An invitation targets a temporary recipient, not a fake user or the administrator's
own notification destination. Notifications and destinations have exactly one owner:
an existing user or a bounded pending recipient. The host-reserved `auth.user_invite`
source can route only its matching invitation to a PRIVATE external SMTP endpoint.
This endpoint cannot receive ordinary notifications or recovery secrets. Existing
delivery leases, retry state, administrator restrictions, and SMTP sensitive-transport
checks apply. The encrypted fragment link is resolved only at final handoff; generic
notification content contains no email address or secret. Invitation messages carry
no marketing unsubscribe header.

`GET /api/auth/user-invites/status` returns availability only. Opening the link does
not consume it. `POST /api/auth/user-invites/accept` accepts `id`, `token`, `username`,
and `password`; email and administrator privileges cannot be supplied. Acceptance
checks the live issuer, installation, configuration, expiry, and single-use token,
then creates a normal account and retires the temporary destination. Sign-in remains
explicit. No SECURE notification destination is automatically created.

Revocation, replacement, expiry, permission withdrawal, disable, uninstall, or a
changed activation invalidate outstanding invitations and cancel unsent deliveries.
Reinstallation requires explicit activation and cannot restore old credentials.
Expired encrypted material is cleared by the existing job loop; bounded records are
removed after an additional day. Reset API 1.1.7 remains compatible.


## Live recovery configuration status (API 1.1.10)

`auth_flows.password_reset.status`, guarded by the existing critical
`auth.password_reset` grant, takes `{}` and returns `registered` plus
`token_lifetime_seconds`. It requires an active administrator and explicit global
grant, and reports only this installation's live flow. Disabled, unhealthy or
ungranted registrations are not enabled; saved plugin preferences are insufficient.
The official contribution places configuration in Administration → Account security.
The invitation contribution uses a separate configuration section there and an
Invite user button/dialog on Administration → Users.
