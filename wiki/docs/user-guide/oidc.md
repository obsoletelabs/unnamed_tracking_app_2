# OpenID Connect / SSO

Configure named OIDC providers under **Settings → Administration → Single sign-on**. First-run Setup can also register an initial provider from setup fields or environment configuration.

## Before configuring

Create an OIDC client in your identity provider using the authorization-code flow.

For a named provider, the callback is:

```text
https://<application-host>/api/auth/oidc/callback/<provider-slug>
```

Use the exact redirect URI displayed in Settings when registering the client.

## Provider settings

Each provider can configure:

- Provider name and slug
- Issuer / discovery URL
- Client ID and client secret
- Scopes, defaulting to `openid profile email`
- Groups claim, defaulting to `groups`
- Optional administrator group
- User matching by email or username
- Whether new users may be created
- Whether the provider must explicitly confirm a verified email
- Login button text, image and colour
- Whether the provider is enabled and shown on the login page
- Whether autostart URLs are enabled

Providers can be added, removed, reordered, and configured independently.

The **Default login method** setting controls whether the normal login page initially prioritises local credentials or SSO.

## Redirect URI

The settings UI generates the redirect URI from the address currently used to access the application and displays it as read-only.

For example:

```text
https://archive.example.com/api/auth/oidc/callback/authentik
```

Register exactly that URI with the identity provider.

## Account matching

After the authorization-code exchange, the application requires an OIDC `sub` and an email identity claim.

**Require verified email** is off by default. When off, false or absent `email_verified` claims retain existing provider compatibility. When enabled, the provider must supply the boolean `email_verified=true`; false, missing, string or numeric claims are rejected before account linking, provisioning, profile changes or session creation.

Providers sharing the same issuer and client ID use the strictest saved or environment requirement across their named and legacy callback aliases. Different clients/providers keep independent policies. `OIDC_REQUIRE_VERIFIED_EMAIL=true` applies to the matching environment-managed provider; omitting it preserves the false default.

Existing users are matched according to **User matching**:

- **Email** matches the local account email.
- **Username** uses the OIDC `preferred_username` or `name` value.

The OIDC subject is linked to the matched account. A subject already linked to another account is rejected as an identity conflict. A linked or username-matched account cannot adopt an email owned by another account. That sign-in fails safely without changing the existing account or opening a session; accounts are never merged by this process.

When **Allow new users** is enabled, an unmatched identity creates a local account. When it is disabled, an unmatched identity cannot sign in until an account is available to match.

## Administrator groups

If **Admin group** is configured, the application checks the configured groups claim. Membership in that group controls the user's administrator flag when the OIDC account is created or updated.

## Environment-managed settings

Deployments can supply supported OIDC values through environment configuration. Environment-managed values can be locked in the settings UI and cannot be overridden there. A saved client secret is never populated into the browser; when one is already configured, leave the client-secret field blank to keep it or enter a replacement.

For ordinary application configuration, use **Settings → OIDC / SSO**.

## Troubleshooting

The public sign-in page, `/login/local`, provider entry pages and callback error screens follow the device's saved theme and palette. A provider uses the app accent by default. Administrators can disable **Use app palette for this sign-in button** and choose a provider color; the host chooses readable black or white button text. Hidden providers remain accessible through their enabled `/login/{slug}` entrypoint. Disabling provider autostart prevents that direct entrypoint, while its explicitly enabled manual sign-in button still works. Saved issuer/credential changes take effect on subsequent sign-ins without a server restart.

- **Callback rejected:** compare the identity provider callback with the exact URI shown in Settings.
- **Provider unavailable:** check the issuer/discovery URL and client credentials.
- **User cannot be created:** check **Allow new users**.
- **Verified email required:** enable this option only when the provider supplies an explicit true verification claim; check its claim mapping and user verification status.
- **Email conflict:** correct the provider email or local account mapping with an administrator.
- **Wrong administrator status:** check the groups claim and **Admin group**.
- **Fields cannot be edited:** the relevant values may be deployment/environment managed.
