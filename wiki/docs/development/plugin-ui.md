# Plugin UI protocol

Native v1.1 plugins can compile their own Vue single-file components against the
shared `context.vue` Vue 3 runtime. `context.ui` exposes `PageHeader`, `UiModal`,
`AppIcon` and `AccountChip` as public components. Bundle the plugin's compiled
components and styles rather than a second framework runtime or host source.
`context.host.confirm(options)` and `prompt(options)` use the native themed
dialogs and reject calls after the contribution is withdrawn. Plugin route
components receive a `context.record_id` query value for owned detail records;
the plugin must validate record ownership in its backend. This is additive to
the existing native activation, action, navigation and cleanup contract.

The frontend host consumes the versioned `PluginUiDocument` contract. Sandboxed,
declarative, and privileged native UI may coexist in one plugin, but each uses a
different host boundary.

## Native primitives

Failed native actions retain the host's HTTP status and reviewed permission,
compatibility or runtime explanation. Validation inputs and raw response bodies
are excluded. Native plugins should distinguish initial loading, configuration
failure with a retry control, and an actual access denial after a successful role
check; an unfinished administrator check is not evidence of insufficient access.

Native action calls can load configuration or poll progress through POST. They
report their loading, success and errors inside the active plugin UI rather than
the host's Settings save indicator. Ordinary plugin settings writes still use
the shared saving feedback.

The v1 contract covers settings fields, validation, secrets, select options, actions, tables, dialogs, menus, and pages. Secret values are write-only and never included in schema defaults or [saved settings responses](#saved-declarative-settings).

Installed plugins are managed through a per-plugin dialog with Overview, Settings, Permissions, and Diagnostics tabs. Its Settings tab controls Plugin Manager update policy and package history. Plugin-provided application pages contain the plugin's functionality and endpoint/profile configuration; the manager links to these pages when available. A plugin may contribute a Settings application section using `settings_sections`; this remains separate from manager permissions, lifecycle and runtime administration.

Plugin names open a larger release overview with publisher information and
expanded, sanitized README content. Installed documentation comes from the
actual installed package, including disabled releases; authenticated readers
and control-plane tokens with `plugins.read` may access this display metadata.
Catalogue review has separate documentation and permission views. A valid
package selection closes the package chooser before opening its review.
Incompatible requirements are highlighted individually, with a prominent
“Unable to install” notice and disabled action. Limited legacy support is
explained separately from installation failures.

Settings contributions may declare `area` (`account`, `preferences` or `administration`), a plain-text `group` label and up to three nested `folders`. Omitted placement preserves the previous behavior: administrator-only sections appear in Administration, other sections in Preferences, under Extensions. Administration placement requires `visibility.admin_only: true`; it does not grant action permissions or expose administrator sections to members. Reserved core section IDs remain protected. The primary sidebar highlights the resolved settings area, including plugin destinations and legacy aliases.

Joining built-in headers requires an additional, independently reviewed permission. Each permission covers its whole area, including every existing header and subtree. Folder names do not create narrower grants:

| Placement | Additional permission | Built-in headers |
| --- | --- | --- |
| Main sidebar | `frontend.placement.sidebar` | Your library, Keep track |
| Administration settings | `frontend.placement.settings.admin` | Server management |
| Account settings | `frontend.placement.settings.account` | Account |
| Preferences | `frontend.placement.settings.preferences` | Preferences, Library, Information |

Ordinary navigation/settings registration and `full_api` do not imply these permissions. Both the API document filter and frontend registry enforce them. Without the required grant, the same owned page remains available beneath Extensions. Custom extension headers and folders remain available without core placement approval. Revocation moves a contribution back to Extensions immediately.

Navigation contributions accept `group` and `folders` too. Settings navigation additionally accepts `area`. `group: "Your library", folders: ["Games", "Challenges"]` joins the existing Games subtree with the sidebar grant; there is no separate Games-subtree permission. Folder controls support keyboard expansion and 44px touch targets in both navigation surfaces.

```json
{"id":"reader-settings","label":"Document reader","page_id":"reader-settings","area":"administration","group":"Documents","visibility":{"admin_only":true}}
```

## Connections

A plugin that links a personal account can declare a `connections` contribution
(Plugin API 1.1.9 or later, 16 at most). The host lists it on **Settings > Account >
Connections** beside the built-in accounts, shows its name, description and status,
and opens the declared page in a popup from **Connect** or **Manage**. The popup is
the plugin's own page, rendered exactly as it is elsewhere, so sign-in, imports and
disconnecting stay on the Connections page instead of a separate sidebar entry.

```json
{"id":"account","label":"Example account","description":"Import a personal library.","page_id":"library","status_action_id":"status"}
```

| Field | Meaning |
| --- | --- |
| `id` | Unique within the plugin. |
| `label` | Row name, 1-64 characters. |
| `description` | Optional row text, at most 256 characters, shown until the plugin reports an account name. |
| `page_id` | A declared page opened in the popup. |
| `status_action_id` | Optional declared action the host runs for the signed-in user when the page loads and when the popup closes. |
| `icon`, `order` | Optional presentation hints. |

The status action returns an object. The host accepts only a literal boolean
`connected` and an optional plain-text `display_name` (trimmed, 80 characters at
most); anything else is treated as not connected, and `ok: false` or a failed action
shows the row as unavailable. The action needs its own declared capability and grant,
for example `plugin.storage`. Without a status action the row shows its description
and no connection state.

Registration requires the `frontend.connections` capability, which is low risk and
independent of `frontend.settings` and `frontend.navigation.*`. Without the grant
the contribution is removed from the API document and ignored by the frontend.
Connections never receive a page-replacement or Settings placement. A document
declaring `connections` below API 1.1.9 is rejected.

API 1.1.11 adds an optional `sync_action_id`, which must reference a declared action.
Such connections appear in the Games page's Sync / import menu. Choosing one opens
the same host dialog and provides the action ID as `plugin.context.initial_action_id`;
the bundled/native frontend starts its existing sync flow through the action bridge.
Ordinary Connections dialogs provide an empty initial action. The menu also retains
the built-in library providers and links to personal Background tasks.

## Navigation and host extensions

The first-class contribution contract supports navigation in the main sidebar, Settings sidebar, administration, game context, and media context. Contributions include an ID, label, icon, ordering, target, and optional visibility conditions. The host only exposes a contribution when the installation has the matching navigation capability.

Legacy pages may still opt into the application sidebar with `navigation.sidebar`.
New navigation entries target exactly one declared page, plugin route, Settings
section, or action. Plugin-owned routes remain under `/plugins/<plugin-id>/...`;
they never claim arbitrary application paths.

Plugins can add declarative content at these allowlisted extension slots:

- `home.after-widgets`
- `game.overview.after-header`
- `game.documents.actions`
- `media.detail.after-header`

An extension references a page in the same UI document. The host renders that page with the native component set and supplies a small context object (such as the current game ID) to actions. Unknown slots, dangling page references, and duplicate contribution IDs are rejected. Sandboxed and declarative UI can coexist, but sandboxed code does not mount into host slots.

The contribution document also defines Settings sections, overlays, dialogs,
contextual actions, plugin routes, and page replacements. A Settings contribution
uses its contribution ID directly, so `id: reader-settings` renders at
`/settings?section=reader-settings`. This is separate from manager configuration
in Plugin Manager and separate from `/plugins/<plugin-id>/<page-id>`.

`sessions` and `admin-sessions` are reserved built-in section IDs. Enhance these
pages using `page_replacements` with `page: sessions` or `page: admin-sessions`
and a declared plugin `page_id`, rather than duplicate settings sections. Each
target needs its own high-risk `frontend.page.replace.<page>` grant. Settings
replacement/placement permissions do not imply either session-page permission.
The administrator section is only mounted for administrators; session-domain
authorization still applies to every operation. Disabled, unhealthy or ungranted
replacements leave the basic manager available. `basic=1` explicitly opens it.

Replacements are page-specific and capability-specific: replacing Home requires
`frontend.page.replace.home`, while replacing Settings requires
`frontend.page.replace.settings`. There is no replace-everything capability.
Conflicts are resolved by ascending `order`, then plugin ID, then contribution ID.
Administrators see the selected plugin and can bypass a Settings replacement to
reach host Settings.

Game, media, and document actions carry a typed resource ID and optional resource
type. The backend confirms that the action is declared for that context and checks
the matching `frontend.context.*` grant before adding the scoped context passed to
the plugin. Client-supplied `_plugin_context` values are discarded.

## Security

UI declarations do not grant capabilities. Actions are sent through the
authenticated gateway and are authorized independently. Declarative slots never
evaluate plugin HTML or JavaScript. Native code executes only for an enabled,
compatible installation with the critical `frontend.native` grant.

See the [Plugin API v1](plugin-api-v1.md) and [Plugin Permissions & Scoped Identities](plugin-permissions.md) pages for the protocol and authorization boundaries.


## Runtime integration

The production `/api/plugins` routes mediate UI documents, frontend assets, ordinary settings, write-only secrets, and declared actions through the authenticated runtime. Browser code never connects to a plugin process directly.

### Saved declarative settings

`PUT /api/plugins/{plugin_id}/settings` merges ordinary values into the
installation's runtime configuration. `GET /api/plugins/{plugin_id}/settings`
reads them back as `{"plugin_id": "…", "values": {…}}` with
`Cache-Control: private, no-store`. Both routes require a live, executable
installation and the same `plugin.settings` grant for the signed-in user, so
anyone who may save the form may reload it; members and administrators are
treated alike, and a user-scoped grant does not authorize other users. Values are
installation-wide rather than per user.

`values` contains only fields declared in the current UI document's `settings`
that are neither `secret` nor `password`, and only when the saved value still
fits the field type and options (an emptied optional number or select is
returned as `""`). Undeclared keys, values stored before a field became secret
and values an update made unrepresentable are omitted. Secret values are never
returned: they are written only through `PUT /api/plugins/{plugin_id}/secrets/{key}`,
and an ordinary save that names a secret field is rejected with 422.

The declarative renderer reads saved values when a page shows ordinary settings
fields. It uses a field's `default` only when no saved value is returned, keeps
fields, Save and actions disabled while loading, and leaves secret fields empty.
If the read fails, for example without the `plugin.settings` grant, the page
reports the reason and shows the declared defaults. Sandboxed and native
frontends manage their own forms and do not trigger this read.

The browser treats Plugin UI documents as untrusted data. Bundled custom frontends
run in a sandboxed iframe on their dedicated plugin page and cannot be mounted into
a host-page extension slot. The backend removes every contribution for which the
current installation/user lacks a grant before returning the document; frontend
checks are defense in depth.

## Privileged native bundle

`native_frontend.entry` and every path in `native_frontend.styles` must live below
`native/` in the verified package. The entry is an ES module exporting `activate`
(or a default activation function). Activation receives a versioned host context:

- `registerComponent(pageId, component)` associates a Vue component with a page
  already declared in `PluginUiDocument`;
- `onCleanup(callback)` registers plugin cleanup;
- `vue` exposes approved Vue composition/rendering primitives;
- `host` exposes navigation, declared action dispatch, plugin settings save, and
  opening the plugin's declared dialogs.

Native components can therefore render pages, Settings sections, extensions,
replacements, and overlays without special-case host code. The host owns the
registration table and stylesheet links. Disable, uninstall, update, permission
revocation, or activation failure removes those registrations and styles. Render
and cleanup errors are isolated so they cannot take down the application shell.

Component instances belong to a plugin ID and page ID. Switching between native
pages (including Settings sections) unmounts the previous page and creates a new
instance, even if both pages share a component. Use Vue's unmount lifecycle to
clean up page-local resources. Context updates within the same page retain the
instance, and page navigation does not reactivate the bundle.

Background mounts and polling share an in-progress contribution refresh, so a
slow initial load can finish instead of being invalidated by every poll. Manager
mutations request fresh data; account changes invalidate pending responses.
Native JavaScript and stylesheet revisions include the verified package digest.
Replacing a package at the same version therefore refreshes its assets and
browser realm, while an unchanged package keeps its mounted components.

Native CSS is loaded only from the separately permissioned native asset endpoint.
Named palettes use the independent low-risk `frontend.themes` contract. Native
and opaque iframe components follow the public cosmetic appearance snapshot;
see [Plugin palettes and appearance](plugin-themes.md). Their execution and CSP
boundaries remain separate.


## Action context

Declarative page actions receive a host-created non-secret `_plugin_context`.
Contextual actions additionally receive the validated game, media, or document
scope described above. Sandboxed frontend actions use the same declared action
table. If an action declares `confirmation`, the host displays that confirmation
before dispatch, including for iframe requests. Secret fields are excluded from
ordinary settings saves and use the separate write-only secret operation.

## Keyboard shortcuts

Plugin API v1.1 adds the low-risk `frontend.shortcuts` grant. A UI document can
declare up to 64 `shortcuts`, each with a stable `id`, a `label`, one to four
`keys`, and exactly one `page_id`, `route_id`, `action_id` or `control` target.
References must belong to that document. Controls are `search` or `create`,
require `when_route_id`, and use a visible `data-shortcut` marker on the page.
Navigation, route and action permissions remain independently enforced.

Native bundles can call
`context.host.registerShortcut({ id, label, keys, group?, paths? }, callback)`.
Registration requires both `frontend.native` and `frontend.shortcuts`, returns
an unregister function, and is removed automatically on disable, permission
withdrawal, account changes or failed activation. Scoped paths must stay inside
the plugin's namespace. Retain stable IDs to preserve personal remaps.

Keys use `CtrlOrMeta`, `Ctrl`, `Meta`, `Alt` and `Shift` modifiers, for example
`Alt+Shift+L` or `CtrlOrMeta+K`. The host resolves overlapping keys by the oldest
enabled binding, with activation order saved to the user's account. Re-enabling
or remapping an active binding creates a new claim. A conflicting newcomer is
disabled and a notice links directly to its key editor; it stays disabled until
the user resolves and enables it. Plugin refreshes/reinstalls retain priority.
Users can enable, disable or remap bindings in Preferences → Keyboard shortcuts;
help, hover hints and the tour use the resulting active keys. Composition,
repeat, AltGraph input, editable fields and host dialogs retain their guards.

Sandboxed pages receive a read-only `keyboard_shortcuts` appearance snapshot.
The public bridge forwards advertised IDs and combinations; the host rechecks
their active state before dispatch. Legacy bridges retain default hints only
while the corresponding original keys are active. Limited v1.0 compatibility
does not grant this v1.1 feature.

The companion repository's example Shortcut Playground deliberately conflicts
with Search and dynamically adds and removes bindings. Collector's Archive owns
Cards, Sets and Bounties shortcuts; they are absent from the core help page.

## Opaque sandbox asset delivery

An opt-in `frontend.inline_assets: true` manifest flag lets a verified package use classic scripts and CSS in a cookie-isolated iframe. The authenticated entry response resolves only relative package assets within `frontend/`, embeds them with a fresh CSP nonce, and escapes raw-text closing tags. Limits are 32 assets and 8 MiB combined content. External URLs, traversal, query/fragment sources and module scripts are rejected. Asset endpoints remain authenticated; no origin/native privilege or unsafe-eval grant is added. The default remains false for existing frontends.

Game Docs reader declarations, document context and the original-download bridge are described in [Scoped document API](plugin-documents.md).


## Administrative user actions (API 1.1.10)

`administration.users.actions` is a generic page contribution rendered as a button
on Administration → Users. Its page title labels the button; clicking opens a
host-owned dialog. The slot requires `frontend.navigation.admin`, and mounts only
for an administrator. Existing contribution/runtime withdrawal removes the button
and closes the active dialog. Domain permissions still apply to every action.

This dialog and dedicated plugin Settings sections can render a bundled frontend
inside the existing opaque-origin sandbox. The host supplies the declared page ID as the
frontend URI's `page` query parameter for presentation only; it grants no authority.
Settings need `frontend.settings`, and joining Administration needs the existing
`frontend.placement.settings.admin` grant. Plugins can provide their own group,
such as Account security, without host-specific plugin IDs.

## Integrated administration settings (API 1.1.11)

`administration.users.settings` places configuration below the Users controls;
`administration.password-policy.after` places it below Password policy. Both require
`frontend.settings`, API 1.1.11 and an administrator. They render a plugin's bundled
frontend inline without another plugin title or frame border. The existing sandbox,
CSP, appearance bridge, resize bounds and per-action capability checks still apply.
They also support existing declarative/native pages. No plugin-specific IDs are built
into the host. Users action buttons sit beside Create user and use host button styling.
