/** Resolve the `plugin=` Settings query parameter into a safe plugin id (or null). */
export function deepLinkedPluginId(value: unknown): string | null {
  const raw = Array.isArray(value) ? value[0] : value;
  if (typeof raw !== "string") return null;
  const id = raw.trim();
  return id && id.length <= 200 && /^[\w.\-:@/]+$/.test(id) ? id : null;
}

/** True when `entries` contains the plugin, installed or from a catalogue. */
export function hasPluginEntry(
  entries: readonly { plugin_id: string }[],
  pluginId: string | null,
): boolean {
  return !!pluginId && entries.some((entry) => entry.plugin_id === pluginId);
}

/** Settings sections only administrators may open; others get a safe fallback. */
export const ADMIN_ONLY_SETTINGS_SECTIONS: ReadonlySet<string> = new Set([
  "plugins",
  "themes",
]);

/**
 * Whether a Settings location must fall back to the default page for this viewer.
 * Never distinguishes unknown from unauthorized plugins, so existence cannot leak.
 */
export function settingsLinkNeedsFallback(
  isAdmin: boolean,
  section: string,
  area: unknown,
): boolean {
  if (isAdmin) return false;
  return area === "administration" || ADMIN_ONLY_SETTINGS_SECTIONS.has(section);
}
