# Decision brief: project name and logo

Status: **brief for a product decision** (task [#268](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/268), part
of [#248](https://github.com/obsoletelabs/unnamed_tracking_app_2/issues/248)). It does **not** choose a name, rename any
repository, change publisher trust, replace any default asset or publish anything. No new branding-configuration layer is
proposed: the application already has one.

## 1. Two different things called "branding"

| | Instance branding (exists) | Project identity (this brief) |
| --- | --- | --- |
| What | the name and icon an **administrator** sets for *their* installation | the name, logo and package/publisher identity of the **project** |
| Where | Settings › Administration › App branding (`api/routes/branding.py`, `core/branding.py`, `DEFAULT_APP_NAME = "Archive"`) | repositories, images, docs site, plugin publisher, wiki |
| Changing it | anyone running a server, any time | a project decision with compatibility consequences |

An instance that sets its own name never depends on the project's identity; a project rename must not disturb either.

## 2. Inventory of surfaces

### 2.1 Cosmetic defaults (safe to change with a decision)

| Surface | Today | Notes |
| --- | --- | --- |
| In-app default name | "Archive" (`DEFAULT_APP_NAME`) and the bundled default icon | overridable per instance |
| Wiki site title and description | "Unnamed Tracking App" (`wiki/mkdocs.yml`) | cosmetic; URLs are separate (2.3) |
| README and doc prose | "Unnamed Tracking App", "the app" | search-and-replace, review for context |
| A few UI strings | e.g. the Steam sign-in dialog ("sign in to Unnamed Tracking App") | user-visible copy; branding setting could supply the name |
| Calendar feed | `PRODID:-//Media Tracker//Calendar//EN`, calendar name "Media Tracker" | cosmetic, but visible in calendar apps; changing the name changes the calendar's label for subscribers |
| PWA name and icons | `services/pwa.ts`, `public/favicon.svg`, `icons.svg` | default assets; icon sizes below |

### 2.2 Compatibility-sensitive identifiers (must stay stable, or be migrated deliberately)

| Surface | Why it must not change casually |
| --- | --- |
| Container image names and tags (`ghcr.io/obsoletelabs/unnamed_tracking_app_2`) | existing deployments pull them; a rename needs a redirect period and dual publishing |
| Repository names/URLs (`unnamed_tracking_app_2`, `unnamed_tracking_app_plugins`, `unnamed_tracking_playnite`) | clone URLs, workflow references, wiki links, plugin catalogues, `edit_uri` |
| Plugin publisher identity: `"publisher": "Unnamed Tracking Official"` and key ids such as `unnamed-tracking-examples-2026-10-v1` in `plugin_api/trusted_publishers.json`, plugin id prefix `official.` | **signing and trust**: renaming a publisher is a trust change; signed packages and key ids must remain verifiable. The `official.` id prefix is part of every installed plugin's identity |
| Internal paths and service names (`/var/lib/unnamed-tracking/plugins`, `/run/unnamed-tracking`, `unnamed-tracking-runtime-smoke`, runtime/gateway headers) | on-disk layout of existing installs and operator runbooks |
| Environment variables, API paths, cookie names | public contracts |
| Database and migration identifiers | never a branding matter |
| Documentation URLs and anchors referenced from issues, plugins and external posts | redirects needed |

### 2.3 Distribution

GitHub organisation and repository names, container registry namespace, documentation hosting URL, package names for any
future client libraries, and any domain. Each needs an availability and ownership check before a name is announced.

## 3. Candidate directions (illustrative, not proposals to adopt)

Directions describe *what the name says*, so the decision can be made on intent before names are searched:

| Direction | Intent | Examples of the kind of name |
| --- | --- | --- |
| Library/archive | calm, personal collection | "Archive", "Stacks", "Shelf" |
| Journal/log | tracking as a habit | "Logbook", "Ledger" |
| Playful | approachable, community | one short invented word |
| Neutral descriptive | self-explanatory | "<media> tracker" style |

The in-app default **"Archive"** already exists and is a reasonable working default for instance branding; it is not
evidence that it should be the project name.

## 4. Checks to perform before a decision

For each shortlisted name: trademark search (WIPO, USPTO, EUIPO and the home jurisdiction); GitHub organisation/repository
and container-registry availability; domain and social handles; search-engine collisions (existing software with the same
name); pronunciation and accessibility of the name in the project's main languages; no confusion with the media services the
application integrates with (using a provider's name or logo risks a trademark complaint).

## 5. Logo and asset requirements

* **Ownership and licence.** Commissioned or original work with a written licence; no stock art or AI output whose licence
  does not allow trademark use. Record the licence next to the files. Decide separately whether the logo is
  covered by the repository's licence or by a stricter trademark policy, and state that plainly in the repository.
* **Formats and sizes.** SVG master (single colour and full colour); PNG exports at 16, 32, 48, 64, 128, 180 (Apple touch),
  192 and 512 (PWA, maskable safe zone), 1024 (store/avatars); a 1200×630 social card.
* **Light and dark.** A version that works on dark and on white surfaces (check both Modern and Classic styles in the dark and light themes); a monochrome mark for single-colour
  contexts; never rely on colour alone to carry the name.
* **Accessibility.** Minimum contrast 4.5:1 for any text in the wordmark against its background; clear space of at least the
  mark's cap height; legible at 16 px as a favicon; meaningful `alt` text; no motion in the default asset.
* **Maskable and monochrome PWA icons** where the platform asks for them.

## 6. Recommended rollout (after a decision)

1. Decide the name and licence in the open (an issue with the checks in section 4).
2. Reserve the names (organisation, registry, domain) *before* any announcement.
3. Replace **cosmetic defaults** only (2.1) in one reviewed change; keep the instance-branding system untouched.
4. Dual-publish container images under the old and new names for at least two minor releases, with a notice; keep the old
   repository names as redirects.
5. Leave **publisher identity and key ids** (2.2) unchanged until a separate trust decision: if the publisher name must
   change, add a new key with the new name, keep verifying the old one through its published successor chain, and document
   the overlap.
6. Update docs, then announce.

## 7. Decisions needed

| Question | Recommendation |
| --- | --- |
| Rename the project at all? | Only if a name passes the section 4 checks; "Unnamed Tracking App" is a working title, not a harm |
| Rename repositories and images? | Last, with dual publishing; never in the same change as cosmetic defaults |
| Change the plugin publisher/key identity? | No, unless the owners separately approve a trust migration |
| Who owns the logo and under what licence? | decide before commissioning. Note: the repository currently has no top-level `LICENSE` file, so the licence of the code, the docs and any future logo should be stated first |
