import type { IdentitySources } from "../services/gameIdentity";

export type IdentityAct = (change: () => Promise<void>) => Promise<boolean>;

export interface IdentityTabProps {
  data: IdentitySources;
  busy: boolean;
  act: IdentityAct;
}

export function titleOf(
  data: IdentitySources,
  id: string | null | undefined,
): string {
  if (!id) return "Unknown game";
  return data.titles[id] ?? "Another game";
}

export const RELATIONSHIP_KINDS: { value: string; label: string }[] = [
  { value: "version_of", label: "Version of" },
  { value: "remake_of", label: "Remake of" },
  { value: "remaster_of", label: "Remaster of" },
  { value: "port_of", label: "Port of" },
  { value: "dlc_of", label: "DLC of" },
  { value: "expansion_of", label: "Expansion of" },
  { value: "standalone_expansion_of", label: "Standalone expansion of" },
  { value: "mod_of", label: "Mod of" },
  { value: "modpack_of", label: "Modpack of" },
  { value: "total_conversion_of", label: "Total conversion of" },
  { value: "series_member", label: "Part of series" },
  { value: "collection_member", label: "Part of collection" },
  { value: "related_to", label: "Related to" },
  { value: "custom", label: "Custom…" },
];

export const DISPLAY_CHOICES: { value: string; label: string }[] = [
  { value: "default", label: "Follow my library rule" },
  { value: "link", label: "List beside" },
  { value: "nest", label: "Nest under" },
  { value: "hidden", label: "Hide from library" },
];

export function kindName(kind: string, label: string): string {
  if (kind === "same_game") return "Same game";
  if (kind === "custom") return label || "Custom";
  return RELATIONSHIP_KINDS.find((k) => k.value === kind)?.label ?? kind;
}

// The same relationship read from the other end: Tidebound is "DLC of" Harbor,
// so Harbor lists Tidebound as its "DLC".
const INVERSE_KIND_LABELS: Record<string, string> = {
  version_of: "Other version",
  remake_of: "Remake",
  remaster_of: "Remaster",
  port_of: "Port",
  dlc_of: "DLC",
  expansion_of: "Expansion",
  standalone_expansion_of: "Standalone expansion",
  mod_of: "Mod",
  modpack_of: "Modpack",
  total_conversion_of: "Total conversion",
  series_member: "In series",
  collection_member: "In collection",
  related_to: "Related to",
};

export interface RelatedGameLink {
  relationshipId: string;
  gameId: string;
  title: string;
  /** how the other game relates to this one, read from this page */
  label: string;
  /** "outgoing": this game is the DLC/remake/version; "incoming": the other one is */
  direction: "outgoing" | "incoming";
}

/**
 * The live relationships of a game other than its merged versions, readable
 * from either end, so a DLC links to its base game and the base game back to
 * its DLC (and likewise for remakes and editions). Base games, the things
 * this game belongs to, come first.
 */
export function relatedGameLinks(
  data: IdentitySources,
  gameId: string,
): RelatedGameLink[] {
  const mine = new Set([
    gameId,
    data.umbrella_id,
    ...data.versions.map((v) => v.game_id),
  ]);
  const links: RelatedGameLink[] = [];
  for (const r of data.relationships) {
    if (r.kind === "same_game" || r.ended_at !== null) continue;
    if (!r.from_id || !r.to_id) continue;
    const fromMine = mine.has(r.from_id);
    const toMine = mine.has(r.to_id);
    if (fromMine === toMine) continue;
    if (fromMine)
      links.push({
        relationshipId: r.id,
        gameId: r.to_id,
        title: titleOf(data, r.to_id),
        label: kindName(r.kind, r.label),
        direction: "outgoing",
      });
    else
      links.push({
        relationshipId: r.id,
        gameId: r.from_id,
        title: titleOf(data, r.from_id),
        label:
          r.kind === "custom"
            ? r.label || "Related"
            : (INVERSE_KIND_LABELS[r.kind] ?? kindName(r.kind, r.label)),
        direction: "incoming",
      });
  }
  return links.sort(
    (a, b) =>
      Number(a.direction === "incoming") - Number(b.direction === "incoming") ||
      a.label.localeCompare(b.label) ||
      a.title.localeCompare(b.title),
  );
}
