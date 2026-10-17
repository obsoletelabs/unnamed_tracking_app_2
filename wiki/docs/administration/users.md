# Users

Administrators can list, create, update, and delete local users from Settings. A user has a unique username/email identity, an active state, and an administrator flag. Deactivation prevents normal access without immediately deleting library data.

The configured primary user is reconciled at application startup when `PRIMARY_USER_USERNAME`, `PRIMARY_USER_EMAIL`, and `PRIMARY_USER_PASSWORD` are present. Treat those values as deployment secrets and rotate them through the supported configuration path.

## Usernames

New and renamed usernames are 1-100 ASCII letters, digits, underscores, hyphens or dots, with no spaces. The profile form, the administrator "Create user" form, the invitation form and the first-administrator step of setup check this rule while you type and when you leave the field, and name the exact problem (for example `Username cannot contain spaces.`, `Username is too long: use at most 100 characters (this one has 101).`, or `Enter a username.`). The message is attached to the field for screen readers and the field is marked invalid.

The server applies the same rule when you save and is authoritative. Whether a name is already taken is deliberately not checked while typing, because that would let anyone probe which accounts exist. A profile or administrator save that collides with another account reports `That username is already taken.` (or `That email address is already in use.`) at the matching field. The public invitation form keeps one combined message for either collision so it cannot be used to discover accounts. No names are reserved. Existing usernames that predate the rule (for example ones containing spaces or `@`) keep working for sign-in, and keeping your current username unchanged on the profile form is never flagged.

Users can update their own profile and manage their own API keys. API-key values are shown only when created; revoke unused keys rather than sharing them. OIDC-linked identities follow the configured provider and claim rules.

**Settings → Administration → Session Manager** provides built-in cross-user browser-session inspection, search, state/user filters, and confirmed revocation of one session, all sessions for a selected user, or all server sessions. Bulk revocation includes records hidden by filters, including expired but unrevoked sessions. Revoking your own current session returns you to sign-in. Only administrators can use these operations.

The built-in manager shows state, IP, browser details and timestamps without GeoIP databases. The official Session Manager plugin can replace just this section with location, network, anomaly, map and database controls after its separate administrator-session replacement permission is approved. Disabling the plugin restores the basic manager.

Deleting a user is destructive and can affect user-owned library data. Take a verified backup first. Plugin grants scoped to one user do not authorize another user, and a plugin session/document request is checked again against the authenticated user at the gateway.
