import { failedRequest } from "./apiError";

// Where each metadata value of a game came from, which sources disagree and
// which one the person prefers (#348, #349). The backend decides what a
// refresh does with a preference; the page only lists and sets it.

export interface FieldSourceValue {
  sourceKey: string;
  kind: "provider" | "copy";
  label: string;
  value: unknown;
  current: boolean;
}

export interface FieldSourceEntry {
  origin: string | null;
  provider: string | null;
  locked: boolean;
  preferred: string | null;
  sources: FieldSourceValue[];
}

export type FieldSources = Record<string, FieldSourceEntry>;

/* eslint-disable @typescript-eslint/no-explicit-any -- backend JSON */
export function mapFieldSources(raw: Record<string, any>): FieldSources {
  const out: FieldSources = {};
  for (const [field, entry] of Object.entries(raw ?? {})) {
    out[field] = {
      origin: entry.origin ?? null,
      provider: entry.provider ?? null,
      locked: !!entry.locked,
      preferred: entry.preferred ?? null,
      sources: (entry.sources ?? []).map((source: any) => ({
        sourceKey: source.source_key,
        kind: source.kind,
        label: source.label,
        value: source.value,
        current: !!source.current,
      })),
    };
  }
  return out;
}
/* eslint-enable @typescript-eslint/no-explicit-any */

export async function fetchFieldSources(
  gameId: string,
  signal?: AbortSignal,
): Promise<FieldSources> {
  const response = await fetch(`/api/game/${gameId}/field-sources`, {
    credentials: "include",
    signal,
  });
  if (!response.ok) throw await failedRequest(response);
  return mapFieldSources(await response.json());
}

export async function preferFieldSource(
  gameId: string,
  field: string,
  sourceKey: string,
): Promise<{ applied: boolean }> {
  const response = await fetch(
    `/api/game/${gameId}/field-preferences/${field}`,
    {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_key: sourceKey }),
    },
  );
  if (!response.ok) throw await failedRequest(response);
  return { applied: !!(await response.json()).applied };
}

export async function clearFieldSource(
  gameId: string,
  field: string,
): Promise<void> {
  const response = await fetch(
    `/api/game/${gameId}/field-preferences/${field}`,
    { method: "DELETE", credentials: "include" },
  );
  if (!response.ok) throw await failedRequest(response);
}
