# Decision brief: project naming and branding

Status: decision brief for the maintainer, for
[#268](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/268) (part of #248;
upstream Rosefall #10). This page does **not** propose, rank or recommend a name or logo.
It lays out what a naming decision affects, what each layer costs to change, and which
questions the maintainer must answer. It does not authorize renaming repositories,
changing publisher trust or publishing a brand, and it adds no branding configuration.

## 1. Why this matters

The project has no settled name. Several different spellings are in use, one of them is
a placeholder default shown to users, and some identifiers are tied to cryptographic
trust and installed data, so they cannot be renamed casually. Choosing a name late costs
more, because every release adds installed plugins, signed packages, container
deployments and external links that depend on the current identifiers.

The host already lets an administrator set a deployment's own display name and
logo/favicon (`api/routes/branding.py`, `core/branding.py`). That is **user-configured
branding** for a deployment. This brief concerns the **project's default identity**: the
fallback shown before anyone configures anything, plus package, distribution and trust
identifiers. The two must stay distinct, and no new configuration layer is needed.

## 2. Current naming inventory

Counts were taken by text search of the repository at the time of writing and are
approximate. They include documentation and tests.

### 2.1 Spellings in use

| Form | Where it appears (examples) | Approx. files / occurrences |
| --- | --- | --- |
| `Archive` | Default display name `DEFAULT_APP_NAME` (`src/backend/src/core/branding.py`), frontend fallback (`src/frontend/src/state/branding.ts`), page title fallback (`src/frontend/index.html`, `router/index.ts`), example database name `POSTGRES_DB=archive` (`example.env`) | small, concentrated |
| `Unnamed Tracking App` | Wiki site name (`wiki/mkdocs.yml`), README and docs prose, Steam sign-in device name (`features/metadata/games/steam_login.py`) and the dialog copy in `SteamConnectionDialog.vue` | about 17 files, 25 occurrences |
| `unnamed_tracking_app` (underscore) | Container image, repository names, CI workflows, docs, plugin tooling | about 68 files, 522 occurrences (most are documentation records and audit JSON) |
| `unnamed-tracking` / `unnamed_tracking` (hyphen, short) | Plugin data path `/var/lib/unnamed-tracking/plugins`, default PostgreSQL user/db `unnamed_tracking` in compose files, `User-Agent` strings, event `source` values, URN/UUID namespaces for notifications, nginx and entrypoint scripts, `localStorage`/session key `unnamedTracking.startupReturnPath` | about 40 source files, 117 occurrences |
| `UNNAMED_TRACKING_APP_*` environment variables | `UNNAMED_TRACKING_APP_VERSION`, `_PORT`, `_HTTPS_PORT` in compose files and `.env` examples | about 6 files |
| `Unnamed Tracking Official` | Publisher name in `src/backend/src/plugin_api/trusted_publishers.json` (9 entries) | 1 file, security-relevant |
| `Rosefall` / `rosefall-a` | Upstream repository and org (issue provenance, `ghcr.io/rosefall-a/unnamed_tracking_app` image in compose and docs), many `rosefall-469-*` documents | many docs |
| `obsoletelabs` | Current GitHub organization, docs URL, default plugin catalogue URL | about 39 files, 149 occurrences |

Observation: the container image path (`rosefall-a`) and the GitHub organization
(`obsoletelabs`) already differ, so a naming decision also needs to settle which
organization owns published artifacts.

### 2.2 By surface

| Surface | Current value | Kind | Compatibility sensitivity |
| --- | --- | --- | --- |
| In-app default name | `Archive` | Cosmetic default, overridable by admin | Low. Stored name is `NULL` when equal to the default (`branding.py` line setting `branding_name = None` if default), so changing the default changes every deployment that never customized it |
| Default logo/favicon | `DEFAULT_ICON` SVG in `core/branding.py`; frontend `public/favicon.svg`, `icons.svg` | Cosmetic default | Low. Custom uploads are normalized to PNG (bounded to 512 px; PWA icons composed from the logo) |
| Browser title | `Archive` fallback | Cosmetic | Low; also asserted in `src/frontend/src/tests/pageTitle.test.ts` |
| Wiki site name and URL | `Unnamed Tracking App`; `https://obsoletelabs.github.io/unnamed_tracking_app_2/` | Documentation | Medium. The URL derives from org and repository name, so external links break on rename unless redirected |
| GitHub repository | `obsoletelabs/unnamed_tracking_app_2` (with a `_2` suffix); plugins repo `obsoletelabs/unnamed_tracking_app_plugins` | Distribution | High. Default plugin catalogue URL (`api/routes/plugin_manager/catalogues.py`, `example.env`) points at the plugins repository raw path; GitHub redirects renamed repositories but not indefinitely or for raw content in all cases |
| Container image | `ghcr.io/rosefall-a/unnamed_tracking_app` | Distribution | High. User compose files pin it; old tags remain until removed |
| Compose project, services, env vars | `UNNAMED_TRACKING_APP_*`, db defaults `unnamed_tracking` | Deployment contract | High. AGENTS.md says not to change environment-variable contracts casually; renaming the default database or user does not rename existing volumes' databases |
| Filesystem paths | `/var/lib/unnamed-tracking/plugins` | Runtime data location | High. Existing volumes and the runtime sandbox expect it |
| Plugin IDs | `official.*` (for example `official.pwa`, `official.forest`, `official.jellyfin-media-sync`), `example.*`, `plugin.*` | Public identifier | Very high, see section 3 |
| Plugin package format | `.utp` / `.utt` file names embedding IDs (`official.pwa-0.0.1.utp`) | Distribution | High. Names derive from plugin IDs |
| Publisher identity | `"Unnamed Tracking Official"` with key IDs such as `official-example-2026` and `plugin_id_prefixes` | Trust | Very high, see section 3 |
| Outbound identifiers | `User-Agent: unnamed-tracking-app/1.0`, Steam device name | Behavioral | Low to medium. Provider terms or allow-lists may key on the User-Agent |
| Generated identifiers | UUID5 namespaces such as `unnamed-tracking:plugin:{id}` (`plugin_api/gateway.py`, `plugin_manager/catalogues.py`) | Persistent derived IDs | High. Changing the string changes derived notification/media IDs and breaks deduplication of existing records |
| PWA | `official.pwa` plugin, service worker, push assets in `plugin_api/pwa_assets` | Public identifier plus UI | Medium to high. Installed PWAs have names, icons and push subscriptions |
| Browser storage keys | `unnamedTracking.startupReturnPath` | Internal | Low. Renaming loses transient state only |
| API paths | `/api/...` (no product name) | Public API | Not affected by a name |
| Python/npm package names | frontend `package.json` name is `frontend`; backend is not published as a package | Internal | Low; not user-visible |
| Git history, issue and PR references, `rosefall-469-*` docs | Historical | Records | Documentation records; should not be rewritten |

## 3. Constraints

### 3.1 Plugin and package ID stability

- Plugin IDs are namespaced strings (`official.*`) used as primary keys for installed
  state, grants, storage directories, encrypted configuration, provider registrations and
  metadata ordering (`provider_order` stores provider IDs, and uninstalled plugins keep
  their position). The IDs do not contain the project name; they contain a publisher
  namespace (`official`). Renaming the project does not require changing plugin IDs, and
  changing the `official` namespace would orphan installed data.
- The plugin API contract is versioned (`Plugin API v1`). Manifest, canonical payload
  digest, signature format and publisher key handling are security-sensitive per
  AGENTS.md section 6.2 and must not be altered as a side effect of a rename.

### 3.2 Signing and publisher identity

- Publisher trust is anchored in `trusted_publishers.json`: key IDs, public keys, the
  human-readable `publisher` string, allowed `plugin_id_prefixes`, validity windows and
  historical package hashes. The publisher string is data displayed to users; the key and
  prefix are what is verified. A display-name change to the publisher string is a data
  change but is also a trust-surface change and needs the same review as any trust edit.
- Rotating or replacing a signing key is a separate, larger decision (see the existing
  successor-key fields). Nothing in a name change requires key rotation, but an
  organization move might, if the maintainer wants new keys held by a new entity.
- Existing signed packages must continue to verify. Anything that rewrites the
  `publisher` string inside signed manifests would invalidate signatures and must be
  avoided for already published packages.

### 3.3 Distribution and links

- Image names, org/repo names, wiki URL and the plugin catalogue URL are referenced by
  users' compose files, bookmarks and CI. Redirects and dual publishing periods can
  soften a move; they do not remove it.
- Docker volumes and database names created from the current defaults persist across
  upgrades. Changing defaults only affects new installs unless a migration path is written.

### 3.4 Legal and availability checks (to be performed by humans)

None of the following has been done in this brief and none can be done by the repository:

- Trademark searches in relevant jurisdictions and classes (software, entertainment
  tracking).
- Domain name, GitHub organization, container registry namespace and social handle
  availability for each candidate.
- Package registry and search-engine collisions (existing products with similar names, and
  similarity to the names of the metadata providers the app integrates with).
- Whether any candidate name or logo implies affiliation with a third party.

### 3.5 Asset requirements

If a new logo is commissioned or chosen, the following should be settled before adoption:

- **Ownership and license:** who holds copyright, whether the license permits use in the
  app, the documentation, packages and merchandise, and whether the source files are kept
  in the repository. Third-party or generated art needs a recorded license.
- **Formats and sizes:** a vector master (SVG) plus raster exports. Existing code
  normalizes uploaded images to PNG up to 512 px and composes PWA icons from the logo with
  a padded canvas (`core/branding.py`), so the default asset should work at 512 px and
  small sizes down to favicon scale (16 and 32 px), with a maskable-safe variant for PWA
  icons.
- **Light and dark:** the UI has light and dark themes and user-selectable themes, so the
  mark needs legible variants or a single mark that works on both.
- **Accessibility:** sufficient contrast for the wordmark, meaningful alt text and
  `aria-label` strings, and no information conveyed by color alone. The default icon is
  currently an inline SVG served at `/api/branding/default-icon.svg`.
- **Usage rules:** a minimal clear-space and minimum-size note so documentation and
  plugins show it consistently.

## 4. Decision criteria

The maintainer can weigh these in whatever order they choose; this brief assigns no
weights.

1. Legal clearance: trademark and name conflicts.
2. Availability: org, repository, registry namespace and domain.
3. Compatibility cost: breakage for existing deployments, plugins and links.
4. Trust continuity: effect on publisher identity and signature verification.
5. Clarity: whether the name is distinguishable, pronounceable and searchable.
6. Ownership: which organization (`obsoletelabs` or another) should own the name and
   registry namespace.
7. Timing: pre-release flexibility versus the number of installed instances.
8. Contributor and user communication cost.

## 5. Process options

These are options for how to run the decision and rollout, not options among candidate
names. Costs are qualitative.

| Option | What changes | Approximate cost and risk |
| --- | --- | --- |
| A. Keep current naming | Nothing; optionally record the decision and explain the spellings in docs | No migration. Retains the mixed spellings and placeholder default `Archive`. Defers cost |
| B. Change display names only | Default app name and default icon (`core/branding.py`, `state/branding.ts`, `index.html`), wiki site name and prose, outbound device/User-Agent strings | Small code change and tests; deployments that never customized the name will see the new default. No change to IDs, images, paths, env vars or trust data |
| C. Display names plus distribution identifiers | B, plus new GitHub repository/org names, container image path and wiki URL, with redirects and a deprecation period for the old image | Medium to large. Requires external accounts and namespaces, CI changes, documentation updates and user communication; compose files pinned to the old image keep working until it is removed |
| D. Full rename including internals | C, plus env vars, default database and user, data paths, generated-ID namespaces and publisher string | Largest. Needs compatibility shims or migrations for volumes, env variables and persistent derived IDs; risks breaking installed deployments; publisher string changes touch trust data. No technical requirement forces this level |

A staged path (for example B first, then C after availability checks) is possible; choosing
between staged and one-step rollout is itself a decision (question 8).

## 6. Questions the maintainer must answer

1. Is a name change wanted at all, or should the project keep its current naming (option
   A)?
2. If a change is wanted, which candidate names should be put through legal and
   availability checks, and who will perform those checks?
3. Which organization should own the final GitHub organization, repository and container
   registry namespace: `obsoletelabs`, `rosefall-a`, or a new one?
4. Which process option (A, B, C or D) applies, and is a staged rollout acceptable?
5. Should the default `Archive` display name remain the default after the project name is
   chosen, or should it match the project name?
6. Is a new logo needed, who will create it, under what license, and who will own the
   source files?
7. Must the `official.*` plugin namespace and the existing publisher keys remain
   unchanged? (This brief assumes yes; a "no" would open key-rotation and migration work.)
8. May the publisher display string `Unnamed Tracking Official` change for newly signed
   packages while already published packages keep their signed value?
9. What deprecation period is acceptable for the old container image path, wiki URL and
   plugin catalogue URL?
10. Should the environment variables, default database name/user and data paths keep their
    current values permanently for compatibility, even if the project is renamed?
11. Who approves publication of the final identity, and where (issue, discussion, release
    notes) will the decision be recorded?

## 7. Out of scope and sequencing

- Repository or package renames, external publication and default asset replacement wait
  for the maintainer's decision. This page changes none of them.
- No new branding settings, theme or configuration system is introduced; the existing
  administrator branding stays the mechanism for per-deployment names and images.
- The plugins repository (`obsoletelabs/unnamed_tracking_app_plugins`) is a separate
  project. Any change there is coordinated separately and keeps the public contract.

## 8. Acceptance mapping

| #268 criterion | Where addressed |
| --- | --- |
| Concise decision brief without presenting an unapproved identity as final | Whole page; sections 5 and 6 give process options and questions, no names |
| Inventory separates cosmetic defaults from APIs, package IDs, signing/publisher identity, links | Section 2.2 and section 3 |
| Asset ownership/licensing, accessibility, icon sizes, light/dark requirements | Section 3.5 |
| Renames, publication and asset replacement wait for decision; no new branding layer | Sections 1 and 7 |

The issue asks for candidate directions and a recommendation. Per the maintainer's
instruction for this document, the brief deliberately supplies neither; question 2 asks the
maintainer to supply candidates.
