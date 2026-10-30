# Plugin credential storage and isolation modes

This page records where plugin-held credentials (for example Epic refresh
tokens, Discord bot tokens and Jellyfin API keys) live, who can read them in each
isolation mode, and what is and is not guaranteed. It describes the current
implementation; see [plugin storage](plugin-storage.md) and
[plugin runtime isolation](plugin-runtime.md) for the mechanisms.

## Where credentials live

| Data | Location | Written by |
| --- | --- | --- |
| Plugin secrets and private state | `<plugin-root>/.storage/<plugin-id>/data/…` (mode `0600` files in `0700` directories) | Plugin Runtime, on behalf of the plugin via `storage.put` |
| Plugin configuration | `<plugin-root>/.configuration/<plugin-id>.json` | Plugin Runtime |
| Host-held credentials (API keys, OIDC secrets) | Host database, encrypted with `SECRET_KEY` | Host backend |

Credentials are stored as files in the runtime volume, not encrypted by the
runtime. They are not stored in the host database, and the host backend never
mounts the runtime volume.

## Isolation modes

### Bubblewrap (default, supported for untrusted plugins)

Each worker runs in its own namespace. The runtime volume's storage is **not
mounted** into the worker (`/plugin-data` is an empty tmpfs), the package is
read-only, `.settings.json` is masked, and all capabilities are dropped. A worker
reaches its own namespace only through the broker (`plugin.storage` must be
granted and is rechecked per call). One plugin cannot read another's files.

### Reduced isolation (fallback when bubblewrap is unavailable)

Enabled by an administrator acknowledgement in the Plugin Manager, or by the
`NONBUBBLE_ENV` deployment override. The worker is an ordinary child process of the
runtime, **running as the same operating-system user** with the same filesystem view.

Consequences, stated plainly:

* a worker *can* read every plugin's storage namespace and configuration, because
  file permissions only separate the runtime user from other users;
* a worker *can* read the runtime's own process environment through `/proc`;
* resource limits and the container boundary still apply, but nothing separates one
  plugin from another.

The runtime does keep these guarantees in reduced mode: workers are started with an
explicit environment, so `PLUGIN_RUNTIME_TOKEN`, `SECRET_KEY` and other runtime
variables are not inherited (covered by a test); the host backend and database are
not reachable from the runtime volume; and permissions granted through the Plugin API
are still enforced for API calls.

**Reduced isolation is therefore not a confidentiality boundary between plugins.**
Install only plugins you trust equally with every credential held by every other
installed plugin. Credential encryption by the runtime would not change this, since
its key would be readable from the same process context.

## Decisions

* Credentials stay in the runtime volume, with `0600`/`0700` permissions, as today.
  Moving them into the host database would give the host a role it does not need and
  would not help against a same-user worker.
* Reduced isolation remains an explicit, administrator-acknowledged exception and
  the Plugin Manager notice states it. Per-plugin operating-system users would
  require running the runtime as root and are out of scope.
* Credential continuity: nothing about this model changes the stored format, so
  upgrades, rollback, reinstall and disable/enable keep existing credentials
  ([plugin storage](plugin-storage.md#backuprestore)).

## Tests

* `src/plugin-runtime/tests/test_storage.py` — namespace isolation, traversal and
  symlink rejection, backup/restore.
* `src/plugin-runtime/tests/test_isolation_acknowledgement.py` — acknowledgement
  persistence and that reduced-isolation workers do not inherit runtime secrets.
