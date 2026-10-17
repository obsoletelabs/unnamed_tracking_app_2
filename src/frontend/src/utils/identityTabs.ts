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
