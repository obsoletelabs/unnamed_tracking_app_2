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
  releases: "Releases",
  series: "Series",
  tags: "Tags",
};

/** Sources disagree when at least two of them report different values. */
export function sourcesDisagree(entry: FieldSourceEntry | undefined): boolean {
  if (!entry) return false;
  return new Set(entry.sources.map((s) => JSON.stringify(s.value))).size > 1;
}

/**
 * A preference that the value shown does not follow: the preferred source is
 * not the one that supplied the current value. The backend only stores the
 * winning value of a merge, so a preferred source that lost has nothing to
 * apply; a lock or a value edited by hand also outranks a preference.
 */
export function preferenceNotInEffect(
  entry: FieldSourceEntry | undefined,
): boolean {
  if (!entry?.preferred) return false;
  const current = entry.sources.find((s) => s.current);
  return current?.sourceKey !== entry.preferred;
}

/**
 * The label beside a field shows when there is something to act on or to
 * know: sources that disagree, a lock, or a preference the value does not
 * follow. A single source that nobody changed stays quiet; the Sources panel
 * still names it.
 */
export function showsFieldSource(entry: FieldSourceEntry | undefined): boolean {
  if (!entry) return false;
  return sourcesDisagree(entry) || entry.locked || preferenceNotInEffect(entry);
}

export interface FieldProvenanceRow {
  field: string;
  label: string;
  /** who supplied the value shown now */
  source: string;
  locked: boolean;
  /** the preferred source's label, when one is set */
  preferred: string | null;
  preferenceNotInEffect: boolean;
  disagree: boolean;
}

/** Every known field with a recorded source, in the order of FIELD_LABELS. */
export function fieldProvenance(sources: FieldSources): FieldProvenanceRow[] {
  return Object.keys(FIELD_LABELS)
    .filter((field) => sources[field])
    .map((field) => {
      const entry = sources[field];
      return {
        field,
        label: FIELD_LABELS[field],
        source: currentSourceLabel(entry),
        locked: entry.locked,
        preferred: entry.preferred
          ? sourceLabelFor(entry, entry.preferred)
          : null,
        preferenceNotInEffect: preferenceNotInEffect(entry),
        disagree: sourcesDisagree(entry),
      };
    });
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
