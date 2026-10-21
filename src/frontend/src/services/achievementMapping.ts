import type { Achievement } from "../types/game";
import { normalizeKind } from "../utils/achievements";

const unixSecondsToIso = (seconds: number | null): string | null =>
  seconds === null ? null : new Date(seconds * 1000).toISOString();

export interface BackendAchievement {
  id: string;
  provider: string;
  name: string;
  description: string | null;
  icon_url: string | null;
  unlocked: boolean;
  unlocked_at: number | null;
  locked_icon_url?: string | null;
  sources?: {
    game_id: string;
    copy: string;
    store: string | null;
    provider: string;
    unlocked: boolean;
    unlocked_at: number | null;
  }[];
  medal?: string | null;
  points?: number | null;
  tier?: string | null;
  hidden?: boolean | null;
  global_percent?: number | null;
  progress_current?: number | null;
  progress_target?: number | null;
}

export function mapAchievements(raw: BackendAchievement[]): Achievement[] {
  return raw.map((a) => ({
    id: a.id,
    name: a.name,
    description: a.description,
    unlocked: a.unlocked,
    unlockedAt: a.unlocked ? unixSecondsToIso(a.unlocked_at) : null,
    provider: a.provider,
    iconUrl: a.icon_url,
    lockedIconUrl: a.locked_icon_url ?? null,
    sources: (a.sources ?? []).map((s) => ({
      gameId: s.game_id,
      copy: s.copy,
      store: s.store,
      provider: s.provider,
      unlocked: s.unlocked,
      unlockedAt: s.unlocked ? unixSecondsToIso(s.unlocked_at) : null,
    })),
    points: a.points ?? null,
    tierOverride:
      a.medal === "bronze" || a.medal === "silver" || a.medal === "gold"
        ? a.medal
        : null,
    kind: normalizeKind(a.tier),
    hidden: a.hidden ?? false,
    rarityPercent: a.global_percent ?? null,
    progressCurrent: a.progress_current ?? null,
    progressTarget: a.progress_target ?? null,
  }));
}
