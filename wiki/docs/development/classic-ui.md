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

The game hero/eight-tab and richer editor layouts remain the next shared editor
workstream (#143/#144). Keep new behavior in the current host components; no
Classic page copies or independent provider clients are needed.

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

Visual tests should confirm the selected control, disabled state, focus ring and
foreground contrast. Behavioral tests should exercise the shared workflow once,
with additional appearance coverage only where presentation changes could affect
interaction. Prefer meaningful regressions over tests that restate CSS literals.
