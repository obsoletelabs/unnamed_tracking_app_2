# Users

Administrators can list, create, update, and delete local users from Settings. A user has a unique username/email identity, an active state, and an administrator flag. Deactivation prevents normal access without immediately deleting library data.

The configured primary user is reconciled at application startup when `PRIMARY_USER_USERNAME`, `PRIMARY_USER_EMAIL`, and `PRIMARY_USER_PASSWORD` are present. Treat those values as deployment secrets and rotate them through the supported configuration path.

Users can update their own profile and manage their own API keys. API-key values are shown only when created; revoke unused keys rather than sharing them. OIDC-linked identities follow the configured provider and claim rules.

**Settings → Administration → Session Manager** provides built-in cross-user browser-session inspection, search, state/user filters, and confirmed revocation of one session, all sessions for a selected user, or all server sessions. Bulk revocation includes records hidden by filters, including expired but unrevoked sessions. Revoking your own current session returns you to sign-in. Only administrators can use these operations.

The built-in manager shows state, IP, browser details and timestamps without GeoIP databases. The official Session Manager plugin can replace just this section with location, network, anomaly, map and database controls after its separate administrator-session replacement permission is approved. Disabling the plugin restores the basic manager.

Deleting a user is destructive and can affect user-owned library data. Take a verified backup first. Plugin grants scoped to one user do not authorize another user, and a plugin session/document request is checked again against the authenticated user at the gateway.
