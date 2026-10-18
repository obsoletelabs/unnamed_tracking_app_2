# Administration

Administrators manage users, deployment settings, provider credentials, scheduled jobs, imports/exports, backups, and plugins from Settings.

## Settings sections

Administration separates **Users**, **Single sign-on**, **Server integrations**, **Limits**, **Developer tools**, **App branding**, **Plugins**, **Background tasks** and **Storage & usage**. Each section has its own Settings deep link and consistent page heading. Older `section=admin&tab=...` links resolve to the corresponding section. These server controls remain administrator-only.

## Upload limits

Open **Administration → Limits** to change all five environment-backed caps: images/general files (`MAX_UPLOAD_SIZE_MB`), documents in a game's Docs tab (`MAX_DOC_SIZE_MB`), save archives (`MAX_SAVE_ARCHIVE_SIZE_MB`), video clips and soundtracks (`MAX_CLIP_SIZE_MB`), and world saves/modpacks (`MAX_WORLD_SAVE_SIZE_MB`). Values are whole megabytes, saved for the deployment and enforced on new uploads immediately. Existing files are retained. **Reset all to server defaults** clears the overrides and uses the current environment configuration.

The nullable migrations preserve existing provider credentials, branding and the earlier general-upload override. Unconfigured limits keep their environment values. Docs used to share the images/general cap; they now follow the documents cap only, so a deployment that lowered the general cap to restrict Docs should set the documents cap as well. This page edits application upload caps; plugin package verification and per-plugin runtime quotas retain their existing security boundaries.

Each cap resolves in this order: a nonempty, valid environment value (`MAX_UPLOAD_SIZE_MB` and its siblings) always wins and shows as locked, with the variable name, in Administration → Limits; otherwise a value saved there is used; otherwise the built-in default applies. A set but invalid environment value (not a whole number of at least 1 MB) is ignored and flagged, so it never silently changes the effective limit. Saving a request that names a locked cap is rejected as a whole, so no other source changes. Clearing a saved override only affects caps that are not locked, and a saved override is kept (but inactive) while its environment value is set.

These are the application's own feature caps. The bundled production Nginx lets authenticated uploads through to the application, which enforces them; any additional proxy or CDN in front of it has its own request-size limit (for example `client_max_body_size`, default 1 MB) that must allow the largest configured cap, including a little multipart overhead, or uploads fail before reaching the application. See [Production Docker](../deployment/production-docker.md).

## Operational checklist

1. Configure HTTPS, secure cookies, a unique Fernet `SECRET_KEY`, database credentials, and a unique plugin-runtime token.
2. Create only the users and API keys that are needed.
3. Configure metadata provider credentials under **Metadata → Sources & API keys**, choosing **System default** for shared values. Configure OIDC separately under **Single sign-on**; its environment-managed values remain authoritative.
4. Review scheduled-job intervals and backup status.
5. Install plugins only from a reviewed package, inspect every requested permission, and keep untrusted-package confirmation enabled.
6. Monitor application logs and per-plugin structured diagnostics without copying secrets into support reports.

See [Users](users.md), [App branding](branding.md), [Scheduled tasks](tasks.md), [Plugin administration](plugins.md), and [Startup and troubleshooting](../deployment/startup-and-troubleshooting.md).
