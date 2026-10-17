import { failedRequest } from "./apiError";

// Library layout, umbrella overview and identity management (#101). Values
// shown as "overall" come from the backend's reconciliation; the UI never
// sums or merges them itself.

export type RelationshipDisplay = "default" | "link" | "nest" | "hidden";
export type VersionDisplay = "grouped" | "separate" | "hidden";
export type VersionMode = "grouped" | "separate";

export interface LayoutMember {
  gameId: string;
  display: VersionDisplay;
  relationshipId: string | null;
}
export interface LayoutGroup {
  umbrellaId: string;
  members: LayoutMember[];
}
export interface LayoutRelation {
  id: string;
  kind: string;
  label: string;
  fromId: string;
  toId: string;
  display: "link" | "nest" | "hidden";
}
export interface LibraryLayout {
  mode: VersionMode;
  rules: Record<string, string>;
  groups: LayoutGroup[];
  related: LayoutRelation[];
}

export interface VersionOverview {
  game_id: string;
  title: string;
  label: string | null;
  edition: string | null;
  platform: string | null;
  source: string | null;
  status: string;
  is_primary: boolean;
  display: RelationshipDisplay;
  relationship_id: string | null;
  owned: boolean;
  playtime_seconds: number;
  playtime_basis: "reconciled" | "record";
  last_played_at: number | null;
  achievements: { unlocked: number; total: number; manual_total: number };
  completed: boolean;
  completion_user_set: boolean;
  sources: string[];
  last_observed_at: number | null;
}

export interface UmbrellaOverview {
  game_id: string;
  umbrella_id: string;
  aggregate: {
    playtime_seconds: number;
    playtime_is_sum_of_versions: boolean;
    last_played_at: number | null;
    version_count: number;
    owned_count: number;
    owned_platforms: string[];
    completion: {
      summary: string;
      completed: number;
      of: number;
      partial: boolean;
      all_completed: boolean;
    };
    achievements: {
      total: number;
      unlocked: number;
      percent: number;
      summary: string | null;
    };
  };
  versions: VersionOverview[];
}

export interface SourceObservation {
  observation_id: string;
  game_id: string;
  source: string;
  source_kind: string;
  scope: string;
  connection_label: string | null;
  disconnected: boolean;
  external_id: string;
  title: string | null;
  platform: string | null;
  edition: string | null;
  availability: string;
  unavailable_since: number | null;
  last_observed_at: number;
  revision: number;
  facts: Record<string, unknown>;
}

export interface IdentityRelationship {
  id: string;
  kind: string;
  label: string;
  from_id: string | null;
  to_id: string | null;
  display: RelationshipDisplay;
  origin: string;
  created_at: number;
  ended_at: number | null;
}

export interface IdentityDecision {
  id: string;
  subject_kind: string;
  first_game_id: string | null;
  second_game_id: string | null;
  observation_id: string | null;
  outcome: string;
  actor_kind: string;
  actor_detail: string | null;
  reason: string | null;
  rule_version: string | null;
  confidence: number | null;
  evidence: { matched?: string[]; conflicts?: string[]; missing?: string[] };
  created_at: number;
  superseded_at: number | null;
}

export interface CounterMember {
  observation_id: string;
  source: string;
  value: unknown;
  observed_at: number | null;
  priority: number;
  selected: boolean;
  available: boolean;
}
export interface CounterView {
  counter_id: string;
  game_id: string;
  metric: string;
  raw: unknown;
  effective: unknown;
  selected_source: string | null;
  members: CounterMember[];
  conflicts: { source: string; value: unknown; reason: string }[];
  adjustment: { offset: number | null; override: number | null } | null;
}

export interface IdentitySources {
  game_id: string;
  umbrella_id: string;
  titles: Record<string, string>;
  versions: {
    game_id: string;
    title: string;
    platform: string | null;
    source: string | null;
    playnite_guid: string | null;
  }[];
  observations: SourceObservation[];
  relationships: IdentityRelationship[];
  decisions: IdentityDecision[];
  counters: Record<string, CounterView[]>;
}

const BASE = "/api/game-identity";
const JSON_HEADERS = { "Content-Type": "application/json" };

async function call(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(`${BASE}${path}`, {
    method,
    credentials: "include",
    signal,
    headers: body === undefined ? undefined : JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await failedRequest(response);
  return response;
}

/* eslint-disable @typescript-eslint/no-explicit-any -- backend JSON */
export function mapLayout(raw: any): LibraryLayout {
  return {
    mode: raw.mode === "separate" ? "separate" : "grouped",
    rules: raw.rules ?? {},
    groups: (raw.groups ?? []).map((g: any) => ({
      umbrellaId: g.umbrella_id,
      members: g.members.map((m: any) => ({
        gameId: m.game_id,
        display: m.display,
        relationshipId: m.relationship_id,
      })),
    })),
    related: (raw.related ?? []).map((r: any) => ({
      id: r.id,
      kind: r.kind,
      label: r.label,
      fromId: r.from_id,
      toId: r.to_id,
      display: r.display,
    })),
  };
}
/* eslint-enable @typescript-eslint/no-explicit-any */

export async function fetchLibraryLayout(
  signal?: AbortSignal,
): Promise<LibraryLayout> {
  return mapLayout(
    await (await call("/library", "GET", undefined, signal)).json(),
  );
}

export async function fetchUmbrellaOverview(
  gameId: string,
  signal?: AbortSignal,
): Promise<UmbrellaOverview> {
  return (
    await call(`/games/${gameId}/overview`, "GET", undefined, signal)
  ).json();
}

export async function fetchIdentitySources(
  gameId: string,
  signal?: AbortSignal,
): Promise<IdentitySources> {
  return (
    await call(`/games/${gameId}/sources`, "GET", undefined, signal)
  ).json();
}

export async function mergeGames(
  sourceId: string,
  targetId: string,
  replaceSeparate = false,
): Promise<void> {
  await call("/merge", "POST", {
    source_id: sourceId,
    target_id: targetId,
    replace_separate: replaceSeparate,
  });
}

export async function unmergeGame(
  gameId: string,
  keepSeparate = true,
): Promise<void> {
  await call("/unmerge", "POST", {
    game_id: gameId,
    keep_separate: keepSeparate,
  });
}

export async function separateGames(
  firstId: string,
  secondId: string,
): Promise<void> {
  await call("/separate", "POST", { first_id: firstId, second_id: secondId });
}

export async function moveObservation(
  observationId: string,
  gameId: string,
): Promise<void> {
  await call(`/observations/${observationId}/move`, "POST", {
    game_id: gameId,
  });
}

export async function createRelationship(input: {
  kind: string;
  fromId: string;
  toId: string;
  label?: string;
  display?: RelationshipDisplay;
}): Promise<void> {
  await call("/relationships", "POST", {
    kind: input.kind,
    from_id: input.fromId,
    to_id: input.toId,
    label: input.label ?? "",
    display: input.display ?? "default",
  });
}

export async function endRelationship(id: string): Promise<void> {
  await call(`/relationships/${id}/end`, "POST");
}

export async function setRelationshipDisplay(
  id: string,
  display: RelationshipDisplay,
): Promise<void> {
  await call(`/relationships/${id}/display`, "PUT", { display });
}

export async function changeRelationship(
  id: string,
  input: { kind: string; label?: string; display?: RelationshipDisplay },
): Promise<void> {
  await call(`/relationships/${id}`, "PUT", {
    kind: input.kind,
    label: input.label ?? "",
    display: input.display ?? null,
  });
}

export async function adjustPlaytime(
  gameId: string,
  seconds: number,
  counterId?: string,
): Promise<void> {
  await call(`/games/${gameId}/metrics/playtime`, "PUT", {
    seconds,
    counter_id: counterId ?? null,
  });
}

export async function clearAdjustment(counterId: string): Promise<void> {
  await call(`/counters/${counterId}/adjustment`, "DELETE");
}

export async function setCounterPriority(
  counterId: string,
  observationId: string,
  priority: number,
): Promise<void> {
  await call(
    `/counters/${counterId}/members/${observationId}/priority`,
    "PUT",
    {
      priority,
    },
  );
}

export async function groupCounters(
  counterId: string,
  sourceCounterId: string,
): Promise<void> {
  await call(`/counters/${counterId}/group`, "POST", {
    source_counter_id: sourceCounterId,
  });
}

export async function ungroupObservation(
  counterId: string,
  observationId: string,
): Promise<void> {
  await call(`/counters/${counterId}/members/${observationId}/ungroup`, "POST");
}
