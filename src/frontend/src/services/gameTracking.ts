import { failedRequest } from "./apiError";

// Manual achievements and completion (#97). The backend keeps these apart from
// what Steam, Playnite and plugins report, so a sync never overwrites them.

export interface ManualAchievement {
  id: string;
  scope: "version" | "umbrella";
  gameId: string | null;
  title: string;
  description: string | null;
  iconUrl: string | null;
  category: string | null;
  position: number;
  achieved: boolean;
  achievedAt: number | null;
  matchKey: string | null;
}

export interface VersionAchievements {
  gameId: string;
  title: string;
  platform: string | null;
  provider: { total: number; unlocked: number };
  manual: { total: number; unlocked: number };
  total: number;
  unlocked: number;
}

export interface AchievementTotals {
  umbrella: {
    total: number;
    unlocked: number;
    percent: number;
    manual: number;
    provider: number;
    summary: string | null;
  };
  versions: VersionAchievements[];
  manual: ManualAchievement[];
}

export interface VersionCompletion {
  gameId: string;
  title: string;
  platform: string | null;
  status: string;
  completed: boolean;
  userSet: boolean;
}

export interface CompletionTotals {
  completed: number;
  of: number;
  partial: boolean;
  allCompleted: boolean;
  summary: string;
  versions: VersionCompletion[];
}

export interface ManualAchievementInput {
  title: string;
  description?: string | null;
  iconUrl?: string | null;
  category?: string | null;
  matchKey?: string | null;
  achieved?: boolean;
  scope?: "version" | "umbrella";
}

const BASE = "/api/game-identity";
const JSON_HEADERS = { "Content-Type": "application/json" };

/* eslint-disable @typescript-eslint/no-explicit-any -- backend JSON */
function mapAchievement(raw: any): ManualAchievement {
  return {
    id: raw.id,
    scope: raw.scope,
    gameId: raw.game_id,
    title: raw.title,
    description: raw.description,
    iconUrl: raw.icon_url,
    category: raw.category,
    position: raw.position,
    achieved: raw.achieved,
    achievedAt: raw.achieved_at,
    matchKey: raw.match_key,
  };
}

function mapCompletion(raw: any): CompletionTotals {
  return {
    completed: raw.completed,
    of: raw.of,
    partial: raw.partial,
    allCompleted: raw.all_completed,
    summary: raw.summary,
    versions: raw.versions.map((v: any) => ({
      gameId: v.game_id,
      title: v.title,
      platform: v.platform,
      status: v.status,
      completed: v.completed,
      userSet: v.user_set,
    })),
  };
}

function mapAchievements(raw: any): AchievementTotals {
  return {
    umbrella: raw.umbrella,
    versions: raw.versions.map((v: any) => ({ ...v, gameId: v.game_id })),
    manual: raw.manual.map(mapAchievement),
  };
}
/* eslint-enable @typescript-eslint/no-explicit-any */

async function call(
  path: string,
  method: string,
  body?: unknown,
): Promise<Response> {
  const response = await fetch(`${BASE}${path}`, {
    method,
    credentials: "include",
    headers: body === undefined ? undefined : JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await failedRequest(response);
  return response;
}

export async function fetchCompletion(gameId: string) {
  return mapCompletion(
    await (await call(`/games/${gameId}/completion`, "GET")).json(),
  );
}

export async function setCompletion(gameId: string, status: string) {
  const response = await call(`/games/${gameId}/completion`, "PUT", { status });
  return mapCompletion(await response.json());
}

export async function releaseCompletion(gameId: string) {
  return mapCompletion(
    await (await call(`/games/${gameId}/completion`, "DELETE")).json(),
  );
}

export async function fetchAchievementTotals(gameId: string) {
  return mapAchievements(
    await (await call(`/games/${gameId}/achievements`, "GET")).json(),
  );
}

export async function createManualAchievement(
  gameId: string,
  input: ManualAchievementInput,
) {
  const response = await call(`/games/${gameId}/manual-achievements`, "POST", {
    title: input.title,
    description: input.description ?? null,
    icon_url: input.iconUrl ?? null,
    category: input.category ?? null,
    match_key: input.matchKey ?? null,
    achieved: input.achieved ?? false,
    scope: input.scope ?? "version",
  });
  return mapAchievement(await response.json());
}

export async function updateManualAchievement(
  id: string,
  input: Partial<ManualAchievementInput>,
) {
  const body: Record<string, unknown> = {};
  if (input.title !== undefined) body.title = input.title;
  if (input.description !== undefined) body.description = input.description;
  if (input.iconUrl !== undefined) body.icon_url = input.iconUrl;
  if (input.category !== undefined) body.category = input.category;
  if (input.matchKey !== undefined) body.match_key = input.matchKey;
  const response = await call(`/manual-achievements/${id}`, "PATCH", body);
  return mapAchievement(await response.json());
}

export async function setManualAchievementEarned(
  id: string,
  achieved: boolean,
) {
  const response = await call(`/manual-achievements/${id}/earned`, "PUT", {
    achieved,
  });
  return mapAchievement(await response.json());
}

export async function removeManualAchievement(id: string): Promise<void> {
  await call(`/manual-achievements/${id}`, "DELETE");
}
