# Classic UI porting and design guide

Classic is a built-in presentation of the shared Obsolete Labs application.
It is not a plugin, separate route tree or fork of the Vue UI. The native Modern
presentation retains the existing `archive-pocket` preference identifier so
saved settings and cosmetic cookies remain compatible.

## Ownership and files

| Concern | Shared implementation |
| --- | --- |
| Account choices and validation | `core/preferences.py`, `services/preferences.ts` |
| Setup/env default | `core/config_registry.py`, `core/app_integrations.py`, setup routes |
| Effective account/browser/installed appearance | `state/uiAppearance.ts` |
| Palette and accessible foregrounds | `services/uiPalette.ts` |
| Cosmetic persistence | `deviceAppearance.ts`, `uiPreferencesCookie.ts` |
| Shared semantic tokens | `styles/tokens.css` |
| Classic typography, shape and presentation | `styles/classic.css` |
| Shared modal, navigation and form behavior | Existing components and composables |
| Full editor frame: hero, direct tabs, bounded body, pinned actions | `components/EditorShell.vue` on `UiModal.vue` |
| Editor sections, grids, fields and hints | `styles/shared/editorForm.css` (included scoped) |
| Staged artwork choices and object URL release | `composables/useStagedArtwork.ts`, `components/game/GameArtPicker.vue` |
| Plugin iframe appearance | `services/pluginAppearance.ts` and public appearance messages |

`data-ui-style="classic"` selects the centralized Classic rules. Palette
application uses the style as an input. The built-in `classic` palette works with
either presentation without setting `data-ui-style="classic"`. Classic presentation
selects this palette by default; legacy Classic/orange choices retain their original
colors. Classic defaults to
Rosefall #469's `#0d0d0d` page, `#1a1a1a` controls and `#d68a34` accent. Colors
are semantic roles; custom values replace them completely. Do not hardcode these
values in page components. Installed themes retain their own styling authority.

## Porting an old Rosefall component

1. Identify its useful behavior and obtain the feature-reconciliation selection.
   Classic alone is not approval to replace metadata, notification or job logic.
2. Find the existing host component and its callers. Extend its shared markup
   and state rather than creating `ClassicGamePage`, a second editor or a new API
   client. Preserve title protection, locked fields, plugin contributions and
   staged enrichment.
3. Map the old visual roles onto shared tokens. Use `--ui-bg`, `--ui-surface`,
   `--ui-surface-2`, `--ui-text`, `--ui-dim`, `--ui-accent`, `--ui-accent-text`,
   `--ui-on-accent` and `--ui-border` in place of `--bg`, `--surface`, `--text`,
   `--text-dim`, `--accent` and `--border`. Use the on-accent foreground for filled
   buttons, rather than assuming dark text works with every custom palette.
4. Replace per-page radii, font sizes and spacing with the relevant shared
   `--ui-radius-*`, `--ui-font-*`, `--ui-control-height`, `--ui-row-padding` and
   edge tokens. Add a shared token when several components need the same role.
5. Use existing `UiModal`, controls, dialogs and keyboard/focus patterns. Retain
   responsive breakpoints and phone targets. Old desktop-only behavior is not
   part of the visual port.
6. Add a small centralized Classic selector only when tokens cannot express the
   visual distinction. Keep feature behavior out of that selector and avoid
   accumulating large per-page override blocks.
7. Verify Modern and Classic, Light and Dark, account/browser persistence,
   custom palettes, compact density, contrast, reduced motion and narrow screens.
   Capture representative screenshots and update the PR when the UI changes.

The #469 field-by-field lookup is adapted through `useMetadataComparison.ts` and
`MetadataComparison.vue`, shared by the current game/media editors and quick-add
review. Form adapters supply their own values, labels and current protection.
They stage checked changes and leave persistence to the existing Save boundary.
The host's progressive search/selection composable remains the only search timer
and session client. Artwork goes through `useMetadataArtwork.ts` and the owned,
selected host preview route; do not introduce browser-side provider probes.

The comparison's surface, selected border, action colors, type and radii use
semantic tokens. Its bounded field area keeps Apply/Cancel available in both
presentations. Modern and Classic share all selection, stale-response, protection
and Save behavior. New form fields should extend the adapter and its definitions,
including the public lock name and artwork role when applicable. Preserve a
populated form value until explicitly checked, and recheck current protection
when applying.

## Full editors

The Rosefall #469 game editor is ported as one shared editor (#143), not a
Classic page. `GameFormModal.vue` keeps every field, the staged lookup and its
existing services; `EditorShell.vue` only arranges them:

- **Hero:** a cover button (it opens the artwork tab), the dialog title as a
  kicker, the live title and a short subtitle. The kicker labels the dialog, so
  dialog names such as "Edit game" stay stable for assistive technology.
- **Direct tabs:** a `tablist` of `tab` buttons with roving focus (arrow keys,
  Home and End) controlling one `tabpanel`. There is no wizard; Save is always
  in the pinned actions and submits the shell's form through `form=`.
- **Bounded body:** `UiModal` `size="editor"` and `bounded` give one fixed
  height, so switching tabs does not resize the dialog; only the body scrolls.
  The tab row scrolls sideways on phones and keeps the active tab in view.
- **Pinned actions and status:** the `actions` slot sits in the dialog footer;
  the `status` slot shows a pinned error above it. The `lead` slot puts a lookup
  or review above whichever tab is open.

State that must survive tab changes lives in the editor's setup, not in panel
components. Panels may use `v-if`, except a costly or stateful one: build it on
first use and keep it with `v-show`, as the artwork tab does for its file inputs
and offered images. Never key the lookup review by tab; it must not remount.
Explicit artwork choices go through `useStagedArtwork`: the newest explicit
choice wins, a cancelled picker keeps the current one, the input is cleared on
click so the same file can be chosen again, and object URLs are revoked when a
choice is replaced or the editor unmounts.

Fields that belong to the record, such as favorite, collections, playtime and
the resume note, travel in the editor's one create/update request, so a save
cannot keep some and lose others. Work that needs another request after the
record exists (artwork uploads) runs only after it is saved, never re-creates
it, and reports a retry through the task notice.

The movie, TV and anime editors (#144) adopt the same frame. `MediaFormShell.vue`
is a thin wrapper that wires `EditorShell` for the three of them: the five tabs
(`MEDIA_EDITOR_TABS`), the pinned Delete / Look up metadata / Cancel / Save actions
(`styles/shared/editorActions.css`, shared with the game editor), the pinned error
and a `lookup` slot placed in the shell's `lead` slot so the staged lookup is
reachable from every tab. `MovieFormModal`, `TVShowFormModal` and `AnimeFormModal`
keep their own fields, services, title protection, season staging and save logic.
The parts the three share are typed and separate, not one large component:

- `MediaTrackingFields.vue` (`part="status"` for Basics, `"journal"` for Tracking):
  status, favorite, dates, rewatches, source, ratings, rank, tags and notes.
- `MediaClassificationFields.vue`: genres, countries, languages, age rating and
  features, with a slot for a media-specific field (anime format).
- `MediaArtworkFields.vue` and `MediaProviderIds.vue`.
- `utils/mediaEditorFields.ts`: the typed `TrackingFields` / `ClassificationFields`
  state, their read/validate conversions (`trackingFrom`, `trackingToInput`,
  `classificationToInput`) and provider-ID validation. These are flat on the
  editor's `fields` object because the metadata comparison offers values by key.

Personal tracking values (Tracking tab) stay separate from what a provider
supplies (Details, Links), so a lookup can only offer provider fields and never
overwrites a rating, date or note.

To port an existing UI contribution into the shared editor:

1. Decide whether it is personal (Tracking), provider metadata (Details), artwork
   or identity (Artwork / Links); add it to the shared component when all three
   media have the field, or to the editor's own panel when only one does.
2. Add the value to the editor's `fields`, load it in `loadFrom...`, and send it in
   the same `create...`/`update...` request. Do not add another save request.
3. Validate with `numberOrNull` / the helpers in `mediaEditorFields.ts` and, if the
   message concerns another tab, switch `tab` before showing the error.
4. If the lookup can offer it, add it to the comparison definitions with its
   locked-field name so manual edits stay protected.
5. Use the tokens in `editorForm.css`; add Classic cues only under `.editor-shell`.
6. Plugin-provided panels and actions attach to the media's page, not this dialog;
   keep their slots in the detail views untouched.
The movie, TV and anime editors (#144) should adopt `EditorShell` and
`editorForm.css` the same way: keep each editor's own fields, services,
protection and seasons/progress logic, and add their tabs as direct panels.

Classic's only editor selectors live in `classic.css` under `.editor-shell`:
filled fields on the secondary surface, outlined secondary/destructive footer
actions and the tab weight. Everything else is tokens.

## Wording in shared UI

Modern and Classic share one template and one string set, so copy is reviewed
once. Use sentence case for titles, buttons and labels (proper names such as TV
and AniList stay as written), say what a saved or empty state means, and phrase
failures as "Could not &lt;action&gt;." with the server's own message taking
precedence over the fallback. Destructive controls name the consequence and ask
first. Keep accessibility names, permission reasons and diagnostics. Never add
copy in `classic.css`. The review record, with before and after screenshots, is
the [Rosefall copy review](rosefall-copy-review.md); `copyConsistency.test.ts`
guards the reviewed screens.

## Designing new UI for both presentations

Choose semantic structure first: heading, grouped fields, help text, actions and
loading/error/empty states. Keep one template. Layout and navigation preferences
continue to govern both presentations. Use theme tokens for visual treatment,
not conditions that change which features a user gets.

Keep editable fields local to the form; keep API calls in services and shared
sessions in composables. A new metadata feature belongs in the generic handler
and public provider contract. An optional provider implementation belongs in the
separate plugin repository. Plugin UI receives the resolved public color tokens,
including Classic, without access to private host state.

For Modern, use the current controls rather than recreating Rosefall's: tab rows
look like `SegmentedTabs` (surface-2 track, accent selection with the on-accent
foreground), fields use the `.ui-field` treatment (surface background, border,
control height), filled primary and soft secondary buttons, and the shared
radius, type and spacing tokens. A dialog that needs more than one screen of
fields uses `EditorShell` with tabs and pinned actions instead of a longer
scrolling page; short dialogs keep `UiModal`'s plain header. Keep primary
actions visible on phones, keep 44px targets, and avoid horizontal page scroll:
only a tab row or an image strip may scroll sideways.

Visual tests should confirm the selected control, disabled state, focus ring and
foreground contrast. Behavioral tests should exercise the shared workflow once,
with additional appearance coverage only where presentation changes could affect
interaction. Prefer meaningful regressions over tests that restate CSS literals.
