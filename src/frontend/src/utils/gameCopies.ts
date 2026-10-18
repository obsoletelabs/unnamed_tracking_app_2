import type { GameCopyLabel } from "../types/game";

// Store names as people know them. Playnite and imports report sources in
// several spellings ("steam", "Epic", "Epic Games Store"); manual entries have
// none or "manual".
const STORES: Record<string, string> = {
  steam: "Steam",
  epic: "Epic",
  "epic games": "Epic",
  "epic games store": "Epic",
  gog: "GOG",
  "gog galaxy": "GOG",
  xbox: "Xbox",
  "xbox game pass": "Xbox",
  "microsoft store": "Microsoft Store",
  "ea app": "EA app",
  origin: "EA app",
  "ubisoft connect": "Ubisoft Connect",
  uplay: "Ubisoft Connect",
  "battle.net": "Battle.net",
  "itch.io": "itch.io",
  psn: "PlayStation",
  playstation: "PlayStation",
  nintendo: "Nintendo",
  "amazon games": "Amazon",
  "minecraft launcher": "Minecraft Launcher",
  playnite: "Playnite",
};

/** The store a copy came from, or null for an entry added by hand. */
export function storeName(source: string | null | undefined): string | null {
  const key = (source ?? "").trim().toLowerCase();
  if (!key || key === "manual" || key === "none") return null;
  return STORES[key] ?? source!.trim();
}

/**
 * How a copy is named wherever several copies of one game appear together:
 * "Steam · PC (Windows)", "Nintendo Switch", "Added manually".
 */
export function copyName(copy: {
  source: string | null | undefined;
  platform: string | null | undefined;
}): string {
  const parts = [storeName(copy.source), copy.platform?.trim() || null].filter(
    (part): part is string => !!part,
  );
  return parts.length ? parts.join(" · ") : "Added manually";
}

/** Items from another copy show where they came from; the page's own do not. */
export function copyBadge(copy: GameCopyLabel | undefined): string | null {
  return copy && !copy.is_main ? copyName(copy) : null;
}
