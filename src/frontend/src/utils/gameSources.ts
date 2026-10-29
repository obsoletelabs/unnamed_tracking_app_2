import type {
  CounterView,
  IdentitySources,
  SourceObservation,
} from "../services/gameIdentity";
import { copyName } from "./gameCopies";
import { reporterName } from "./gameOrigin";
import { dayLabel, hoursLabel } from "./identityDisplay";

/** Priority a preferred source gets; a lower number wins (the default is 100). */
export const PREFERRED_PRIORITY = 10;
export const DEFAULT_PRIORITY = 100;

export interface SourceCopy {
  gameId: string;
  name: string;
}

export interface PlaytimeChoice {
  counterId: string;
  observationId: string;
  /** Another source reports the same counter, so there is something to choose. */
  contested: boolean;
  wins: boolean;
  priority: number;
}

export interface SourceSummary {
  /** The connection's source id: "playnite", "steam", a plugin id. */
  key: string;
  name: string;
  kind: string;
  disconnected: boolean;
  copies: SourceCopy[];
  /** What this source reports: "Playtime 41h 10m", "Achievements 3 / 50"... */
  contributes: string[];
  lastObservedAt: number | null;
  playtime: PlaytimeChoice[];
  /** It wins every playtime counter it shares with another source. */
  preferred: boolean;
  /** It shares a playtime counter it does not win, so it can be preferred. */
  canPrefer: boolean;
}

function contributions(observations: SourceObservation[]): string[] {
  const facts = observations.map((o) => o.facts);
  const first = <T>(read: (f: Record<string, unknown>) => T | undefined) =>
    facts.map(read).find((value) => value !== undefined);
  const out: string[] = [];
  const playtime = first((f) =>
    typeof f.playtime_seconds === "number" ? f.playtime_seconds : undefined,
  );
  if (playtime !== undefined) out.push(`Playtime ${hoursLabel(playtime)}`);
  const lastPlayed = first((f) =>
    typeof f.last_played_at === "number" ? f.last_played_at : undefined,
  );
  if (lastPlayed !== undefined) out.push(`Last played ${dayLabel(lastPlayed)}`);
  const unlocked = first((f) =>
    typeof f.achievements_unlocked === "number"
      ? f.achievements_unlocked
      : undefined,
  );
  if (unlocked !== undefined) {
    const total = first((f) =>
      typeof f.achievements_total === "number"
        ? f.achievements_total
        : undefined,
    );
    out.push(
      `Achievements ${unlocked}${total !== undefined ? ` / ${total}` : ""}`,
    );
  }
  if (facts.some((f) => typeof f.owned === "boolean"))
    out.push(facts.some((f) => f.owned === true) ? "Owned" : "Not owned");
  if (observations.some((o) => o.title || o.edition || o.platform))
    out.push("Title and details");
  return out;
}

/**
 * One row per source (Playnite, a store sync, a plugin) across every copy of a
 * game: what it reports, which copies it covers and whether it is the one the
 * playtime comes from. The choice reuses the counters' own priority, so the
 * advanced dialog and this summary always agree.
 */
export function summarizeSources(data: IdentitySources): SourceSummary[] {
  const groups = new Map<string, SourceObservation[]>();
  for (const observation of data.observations) {
    const list = groups.get(observation.source) ?? [];
    list.push(observation);
    groups.set(observation.source, list);
  }
  const playtimeCounters: CounterView[] = data.counters.playtime ?? [];
  const versionName = new Map(
    data.versions.map((v) => [
      v.game_id,
      copyName({ source: v.source, platform: v.platform }),
    ]),
  );
  const summaries: SourceSummary[] = [];
  for (const [key, observations] of groups) {
    const ids = new Set(observations.map((o) => o.observation_id));
    const playtime: PlaytimeChoice[] = [];
    for (const counter of playtimeCounters) {
      const member = counter.members.find((m) => ids.has(m.observation_id));
      if (!member) continue;
      playtime.push({
        counterId: counter.counter_id,
        observationId: member.observation_id,
        contested: counter.members.filter((m) => m.available).length > 1,
        wins: member.selected,
        priority: member.priority,
      });
    }
    const contested = playtime.filter((p) => p.contested);
    const gameIds = [...new Set(observations.map((o) => o.game_id))];
    const seen = observations
      .map((o) => o.last_observed_at)
      .filter((value): value is number => typeof value === "number");
    const first = observations[0];
    summaries.push({
      key,
      name: reporterName({
        kind: first.source_kind,
        source: key,
        label: first.connection_label ?? key,
        disconnected: first.disconnected,
      }),
      kind: first.source_kind,
      disconnected: observations.every((o) => o.disconnected),
      copies: gameIds.map((gameId) => ({
        gameId,
        name: versionName.get(gameId) ?? "Another copy",
      })),
      contributes: contributions(observations),
      lastObservedAt: seen.length ? Math.max(...seen) : null,
      playtime,
      preferred: contested.length > 0 && contested.every((p) => p.wins),
      canPrefer: contested.some((p) => !p.wins),
    });
  }
  // Playnite first (it is the one people ask about), then by name.
  return summaries.sort(
    (a, b) =>
      Number(b.key === "playnite") - Number(a.key === "playnite") ||
      a.name.localeCompare(b.name),
  );
}

/** Whether the person has anything to choose between. */
export function hasContest(sources: SourceSummary[]): boolean {
  return sources.some((s) => s.playtime.some((p) => p.contested));
}

/**
 * The priority changes that make `key` the preferred source of every playtime
 * counter it shares: it gets the winning priority and the others the default.
 * Only changed members are returned.
 */
export function preferenceChanges(
  data: IdentitySources,
  key: string,
): { counterId: string; observationId: string; priority: number }[] {
  const mine = new Set(
    data.observations
      .filter((o) => o.source === key)
      .map((o) => o.observation_id),
  );
  const changes: {
    counterId: string;
    observationId: string;
    priority: number;
  }[] = [];
  for (const counter of data.counters.playtime ?? []) {
    const members = counter.members.filter((m) => m.available);
    if (members.length < 2 || !members.some((m) => mine.has(m.observation_id)))
      continue;
    for (const member of members) {
      const priority = mine.has(member.observation_id)
        ? PREFERRED_PRIORITY
        : DEFAULT_PRIORITY;
      if (member.priority !== priority)
        changes.push({
          counterId: counter.counter_id,
          observationId: member.observation_id,
          priority,
        });
    }
  }
  return changes;
}
