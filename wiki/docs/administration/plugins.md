# Plugin administration

![Package and permission review](../assets/plugin-manager-review.png)

Only administrators can install, update, enable, disable, retry, inspect diagnostics, revoke all grants, or uninstall plugins.

## Review rules

- Verify plugin ID, publisher, version, compatibility ranges, and dependencies.
- Deny capabilities without a clear, necessary rationale.
- Treat document/session reads as sensitive account data, session revocation as destructive, notification delivery as external disclosure, and `plugin.storage` secrets as write-only credentials.
- Treat signed/trusted, signed/unknown-key, signed/invalid, and unsigned states as distinct. An official catalogue listing is provenance, not cryptographic trust.
- Invalid signatures are blocked. Unsigned and unknown-key packages require explicit acknowledgement.
- Granting a highly privileged capability to an unsigned or unknown-key package additionally requires administrator password re-entry and a second explicit confirmation.
- Re-review permissions introduced by an update.

Permissions are shown as an expandable hierarchy. Approving a parent approves its requested subtree; approving a leaf does not grant its siblings. Required dependencies must already be installed at a compatible version. Available dependencies are shown as separate packages and must receive their own trust and permission review.

For an installed plugin, the Permissions tab shows active, pending, and denied
access together. Choose **Review denied permissions** to select one or more
global scopes in a single review. Every scope starts unchecked; the grant action
stays disabled until at least one is selected. You can also review a single
denied scope from its row. Grant, denial, and revocation refresh the open plugin
view immediately.

## Sources and updates

The manager provides Installed, Updates Available, Available to Install and All
views with search and plugin-supplied tag filters. All combines enabled catalogues
and installed identities without duplicate installed entries. Browse plugin
metadata and readable README content before approval; upload and arbitrary URL
acquisition remain available under advanced installation sources.

Global automatic installation defaults off. Per-plugin policy follows or overrides
it. The daily plugin-update scheduled task still discovers and stages catalogue
releases when automatic installation is disabled. Each release may independently
opt out. New permissions require explicit approval while the old version stays
active. Failed activation restores the previous package and reports the failure.
Details provide update/reinstall/rollback controls and configurable package history.
See [update behavior](../development/plugin-updates.md).

Uploads, direct URLs, and catalogue entries all use the same inspection and confirmation lifecycle. The official catalogue is enabled by default. Additional catalogue records retain their priority, enabled state, trust/provenance metadata, last successful check, and last error. Catalogue trust never bypasses package verification.

Installed URL/catalogue plugins retain source metadata for update checks. The update review shows release notes, dependency changes, and the exact permission delta before replacement. New permissions default to denied. Unverified updates inherit no prior grants and require every requested permission to be reviewed again. A discovered update can be reviewed and applied from its recorded source, and availability is also added to the existing administrator notification feed.

### Replacing a package with another publisher's signature

A package signed by a different verified publisher key than the installed one
(including moving between unsigned and verified) keeps the same lifecycle instance
and plugin-owned storage, but it does not inherit notification trust. Ordinary
updates from another publisher are still rejected; this applies to an explicit
administrator replacement, to the first verified update of an unverified install,
and to a rollback onto a retained release from another publisher. The review shows
a warning first. When the replacement is committed, in the same database
transaction as the grant changes, the host:

* clears SECURE verification, recovery and media consent on the plugin's
  notification destinations, disables them, clears host-held endpoint values and
  retires their endpoint key, so users create and verify them again;
* suppresses queued deliveries, pending verification links and unused recovery
  or invitation credentials;
* revokes the plugin's notification type, provider and authentication-flow
  registrations; the replacement registers them again and server-level provider
  configuration must be repeated.

A replacement signed by the same publisher key keeps all of this. If the commit
or runtime preparation fails nothing changes. If the new release is installed and
then fails its startup health check, the previous package and grants are restored
but the destination state stays retired, because the candidate had run. Plugin
storage and settings are not erased; use uninstall or reinstall-with-purge for that.

## Diagnostics and recovery

Stop retains enablement but leaves the plugin stopped after restart. Disable
retains data, configuration, secrets and grants. Reinstall preserves these and
replaces the exact release. Uninstall and confirmed reinstall-with-purge erase
owned state. Manager configuration and plugin-provided application configuration
are separate. The runtime capability notice accurately reports Bubblewrap or
reduced process isolation. See [lifecycle](../development/plugin-lifecycle.md),
[runtime troubleshooting](../development/plugin-runtime.md) and
[remote management tokens](../development/plugin-management-api.md).

The Diagnostics tab shows process state, last exit code, and up to 200 structured events. Secret-, token-, password-, and webhook-shaped values are redacted. Disable a misbehaving plugin before investigating. Revocation blocks subsequent gateway calls; uninstall additionally removes package/private storage and provider registrations.

Installed inventory loads independently of remote catalogues. During an outage,
identity, installed version and enablement remain visible; current health becomes
unknown. The capability notice explicitly says the runtime is unavailable rather
than inferring sandbox status from its last successful report.

![Installed plugin remains visible while the runtime is offline and catalogues are still refreshing](../assets/plugin-integration/offline-inventory.png)

Diagnostics distinguish unavailable runtime, unknown Bubblewrap capability and
unavailable sandbox. Existing grants remain in the Permissions tab; the outage
does not revoke them or delete data. Reconnect the runtime before attempting
worker operations.

![Actual plugin diagnostics during a runtime outage](../assets/plugin-integration/offline-diagnostics.png)

These screenshots come from the real built frontend in
[installed-plugin conformance](../development/plugin-conformance.md), with a
deliberately stopped runtime and delayed catalogue transport.

Production deployments must use bubblewrap isolation and a unique `PLUGIN_RUNTIME_TOKEN`. `NONBUBBLE_ENV=true` is development-only and must not be used for untrusted plugins.

## Metadata provider extension (1.1.1)

Search and refresh use the hardcoded core providers and optional installed provider plugins
through one shared metadata handler. Core search does not require the plugin runtime.
Provider configuration and health appear in the host's metadata settings.
See [the progressive metadata contract](../development/metadata-providers.md) for
phase separation, scoped credential migration, deadlines and persistence behavior.
