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

## Collapsing and reordering providers

Each provider is a collapsible row. Saved providers open collapsed, showing only the provider name, slug, a status label when the provider is disabled or hidden from the login page, and the reorder and **Remove** controls. Select the provider name, or the arrow beside it, to show or hide its settings. A newly added provider opens expanded so it can be filled in. With more than one provider, **Expand all** and **Collapse all** switch every row at once.

Collapse providers to reorder a long list without scrolling through every setting:

- **Mouse:** drag a provider by its header and drop it on another provider.
- **Keyboard and touch:** use the **↑** and **↓** buttons. Focus stays with the provider that moved, so repeated presses keep moving it, and screen readers announce its new position.

Expanding, collapsing and reordering only change the page. Unsaved edits are kept while a provider is collapsed. Select **Save OIDC settings** to store the new order; the login page lists providers in that order.

![Collapsed providers in Modern](../assets/oidc-provider-collapse/modern-dark-collapsed.jpg)

## Redirect URI

The settings UI generates the redirect URI from the address currently used to access the application and displays it as read-only. Save provider settings from the canonical HTTPS application address. Sign-in uses the saved provider URI or an explicit `OIDC_REDIRECT_URI`; when no URI is saved, it uses the shared **App URL** setting (`PUBLIC_APP_URL` when environment-managed). Request Host and forwarded headers cannot replace those configured addresses. After changing the application origin, save the provider from its new canonical address and update the identity provider registration.

Production providers need a saved HTTPS redirect URI or shared App URL. A request-derived URI is available only for loopback development with `DEBUG=true` or `STARTUP_MODE=development`.

For example:

```text
https://archive.example.com/api/auth/oidc/callback/authentik
```

Register exactly that URI with the identity provider.

## ID-token validation

### Provider network policy

Provider discovery, token, JWKS and UserInfo requests require HTTPS, refuse redirects, use bounded responses and disable environment proxies. Public destinations resolve exclusively to public addresses and connections pin those addresses while retaining TLS hostname verification. Discovery cannot override transport, TLS, proxy, headers or PKCE options.

For a private HTTPS issuer, set `OIDC_PRIVATE_ISSUER_ORIGINS=https://sso.internal:8443` (comma-separated for multiple issuers). Only a configured issuer with that exact scheme, hostname and port receives the exception, including its endpoints on that origin. An exception for one issuer does not grant private access to another issuer or to cross-origin endpoints. Use a trusted CA and a certificate valid for the issuer hostname; TLS verification stays enabled. Plain HTTP is allowed only for loopback destinations when `DEBUG=true` or `STARTUP_MODE=development`.

Authorization-code sign-in uses PKCE S256. Discovery must name the configured issuer exactly (legacy discovery-URL inputs allow that same issuer path with either trailing-slash convention); only supported asymmetric ID-token signature algorithms are accepted. The signed nonce must match even if a provider reports `nonce_supported=false`. UserInfo can fill missing profile information only after ID-token validation and only when its subject matches. Unsigned token-response fields cannot supply identity, verification or administrator-group claims.

Sign-in requires an ID token and the nonce saved when the browser started the
authorization-code flow. Authlib must validate that token successfully before
the application resolves an account or creates a session. Missing tokens/nonces,
invalid signatures and malformed or unavailable signing keys fail sign-in;
a UserInfo response never replaces failed ID-token validation.

The provider must publish valid signing keys at its discovered JWKS endpoint.
Fix the provider's discovery/key configuration when validation fails rather than
disabling verified-email checks. Provider errors return a generic sign-in failure;
application logs record the exception type without copying provider response text.

## Account matching

After the authorization-code exchange, the application requires an OIDC `sub` and an email identity claim.

**Require verified email** is off by default. When off, false or absent `email_verified` claims permit new accounts and unchanged, already bound identities. Linking an existing local account or migrating an old subject binding always requires the boolean `email_verified=true` and the same local email. Changing the email on an already bound identity also requires a verified new email. When enabled, the provider must supply the boolean `email_verified=true`; false, missing, string or numeric claims are rejected before account linking, provisioning, profile changes or session creation.

Providers sharing the same issuer and client ID use the strictest saved or environment requirement across their named and legacy callback aliases. Different clients/providers keep independent policies. `OIDC_REQUIRE_VERIFIED_EMAIL=true` applies to the matching environment-managed provider; omitting it preserves the false default.

Existing users are matched according to **User matching**:

- **Email** matches the local account email.
- **Username** uses the string OIDC `preferred_username` claim and additionally requires the same verified email before linking. A free-form display name cannot select an account.

Accounts bind to the exact configured issuer and opaque, case-sensitive subject, using a versioned digest stored in the existing subject field. Renaming a provider slug preserves this binding; repointing a slug at a different issuer does not. Existing raw or slug-prefixed subjects migrate on the next successful login only with a verified matching email. Subject whitespace is never trimmed and numeric/object identity claims are rejected. Email ownership comparisons ignore case, and ambiguous legacy addresses fail safely.

The OIDC subject is linked to the matched account. A subject already linked to another account is rejected as an identity conflict. A linked or username-matched account cannot adopt an email owned by another account. That sign-in fails safely without changing the existing account or opening a session; accounts are never merged by this process.

When **Allow new users** is enabled, an unmatched identity creates a local account. When it is disabled, an unmatched identity cannot sign in until an account is available to match.

## Administrator groups

If **Admin group** is configured, the application checks the configured groups claim. Membership in that group controls the user's administrator flag when the OIDC account is created or updated.

## Authentik example

Create an OAuth2/OIDC provider and application using authorization code, an asymmetric signing key and an exact redirect URI. Use `openid profile email` scopes and the per-provider issuer mode. Copy the complete issuer, including its trailing slash, from discovery; for example `https://sso.example.com/application/o/archive/`. Its discovery URL is `https://sso.example.com/application/o/archive/.well-known/openid-configuration`.

Enable **Require verified email** when the provider verifies address ownership. Existing local accounts and legacy subject bindings always need that verified matching address before linking. Keep the administrator group empty until its mapping and membership are confirmed. For a private issuer, explicitly allow only its HTTPS origin and install its CA in the backend trust store.

See the [Authentik provider documentation](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/) for provider, signing, issuer and redirect configuration. This implementation is tested with a mock OIDC provider and an ephemeral real HTTPS issuer; those checks do not substitute for acceptance testing a particular live Authentik deployment.

## Security verification

| Boundary | Enforced behavior | Regression coverage |
| --- | --- | --- |
| Browser authorization | PKCE S256, saved nonce, provider-specific state, configured callbacks | Real authorization-code exchange; state replay and wrong-provider callback denial; Host/forwarded-header tests |
| Tokens and keys | Configured issuer, asymmetric signatures, audience/expiry/nonce validation, no UserInfo downgrade | Actual signed invalid-token matrix, signature failure and JWKS rotation |
| Account ownership | Exact issuer/subject binding; verified email for initial/legacy linking; username alone insufficient | Existing-account and issuer-change denials, opaque subjects, email case conflicts and unchanged account/session assertions |
| Provider network | DNS-pinned public HTTPS or an explicit issuer-origin exception; no redirects; 1 MiB responses | Private/metadata address denial, exact-origin/port isolation, hostile discovery options, response bounds and actual certificate validation |
| Errors and secrets | Generic sign-in errors and exception-type-only logs | Provider errors containing token/client-secret text never appear in captured logs |

Run the focused tests from `src/backend` against a dedicated migrated test database:

```bash
python -m pytest tests/test_oidc_callback.py tests/test_oidc_signed_tokens.py tests/test_oidc_network.py tests/test_oidc_login_ui.py -q
```

Deployment administrators choose which issuers and administrator groups they trust. A verified address claim is useful only when that issuer actually verifies ownership. Existing browser sessions retain the application's normal revocation and expiry policy.

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
