# Host-mediated authentication plugins

Plugin API 1.1.7 adds an optional password-reset contribution. It uses the existing
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

Email invitations are a separate implementation increment; this contract does not
create accounts or implement the synthetic invite demonstration as production auth.
