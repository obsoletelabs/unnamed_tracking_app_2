import type {
  FieldSourceEntry,
  FieldSources,
} from "../services/gameFieldSources";

// Presentation rules for the per-field source labels (#348, #349).

export const FIELD_LABELS: Record<string, string> = {
  age_rating: "Age rating",
  description: "Description",
  developer: "Developer",
  features: "Features",
  publisher: "Publisher",
  release_date: "Release date",
  series: "Series",
  tags: "Tags",
};

/** Sources disagree when at least two of them report different values. */
export function sourcesDisagree(entry: FieldSourceEntry | undefined): boolean {
  if (!entry) return false;
  return new Set(entry.sources.map((s) => JSON.stringify(s.value))).size > 1;
}

export function disagreeingFields(sources: FieldSources): string[] {
  return Object.keys(FIELD_LABELS).filter((field) =>
    sourcesDisagree(sources[field]),
  );
}

export function formatSourceValue(value: unknown, limit = 140): string {
  const text = Array.isArray(value) ? value.join(", ") : String(value ?? "");
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

/** The compact label: who supplied the value the game shows now. */
export function currentSourceLabel(entry: FieldSourceEntry): string {
  const current = entry.sources.find((s) => s.current);
  if (current) return current.label;
  if (entry.origin === "user") return "Edited by you";
  return entry.provider ?? "Unknown source";
}

export function sourceLabelFor(
  entry: FieldSourceEntry,
  sourceKey: string,
): string {
  return (
    entry.sources.find((s) => s.sourceKey === sourceKey)?.label ??
    sourceKey.replace(/^(provider|copy):/, "")
  );
}

export interface MetadataSummary {
  /** The preferred source named for the most fields, if any is preferred. */
  preferredLabel: string | null;
  preferredFields: number;
  disagreeing: string[];
}

export function metadataSummary(sources: FieldSources): MetadataSummary {
  const counts = new Map<string, { label: string; fields: number }>();
  for (const entry of Object.values(sources)) {
    if (!entry.preferred) continue;
    const seen = counts.get(entry.preferred) ?? {
      label: sourceLabelFor(entry, entry.preferred),
      fields: 0,
    };
    seen.fields += 1;
    counts.set(entry.preferred, seen);
  }
  const top = [...counts.values()].sort((a, b) => b.fields - a.fields)[0];
  return {
    preferredLabel: top?.label ?? null,
    preferredFields: top?.fields ?? 0,
    disagreeing: disagreeingFields(sources),
  };
}
