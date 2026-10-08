import type { MediaNotification } from "../services/notifications";

type NotificationGroup =
  "episodes" | "seasons" | "releases" | "plugins" | "security";
type Presentation = {
  label: string;
  tone: string;
  group: NotificationGroup | null;
};
const kinds: Record<string, Presentation> = {
  episode_aired: { label: "Episode aired", tone: "amber", group: "episodes" },
  season_started: { label: "Season started", tone: "green", group: "seasons" },
  sequel_announced: {
    label: "New season listed",
    tone: "blue",
    group: "seasons",
  },
  movie_released: {
    label: "Movie released",
    tone: "violet",
    group: "releases",
  },
  game_released: { label: "Game released", tone: "green", group: "releases" },
  game_sale: { label: "Game on sale", tone: "amber", group: "releases" },
  game_price_hit: {
    label: "Price target reached",
    tone: "blue",
    group: "releases",
  },
  plugin: { label: "Plugin", tone: "violet", group: "plugins" },
  plugin_update: { label: "Plugin update", tone: "violet", group: "plugins" },
  session_anomaly: { label: "Security alert", tone: "red", group: "security" },
};
const fallback: Presentation = {
  label: "Notification",
  tone: "blue",
  group: null,
};

export function notificationPresentation(kind: string): Presentation {
  return Object.hasOwn(kinds, kind) ? kinds[kind]! : fallback;
}

export function groupNotifications(items: MediaNotification[]): {
  key: string;
  day: string;
  items: MediaNotification[];
}[] {
  const groups = new Map<
    string,
    { key: string; day: string; items: MediaNotification[] }
  >();
  for (const item of items) {
    const date = new Date(item.eventAt * 1000);
    const day = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    // Group only equivalent content boundaries and keep each durable member intact.
    const key = JSON.stringify([
      day,
      item.groupKey ?? item.id,
      item.source,
      item.eventType ?? item.kind,
      item.purpose,
      item.requiredTrust,
      item.severity,
    ]);
    const existing = groups.get(key);
    if (existing) existing.items.push(item);
    else groups.set(key, { key, day, items: [item] });
  }
  return [...groups.values()];
}

export function notificationDestination(
  n: Pick<MediaNotification, "mediaType" | "mediaId">,
): string | null {
  if (!n.mediaId) return null;
  const roots: Record<string, string> = {
    game: "/games",
    movie: "/movies",
    tv: "/tv",
    anime: "/anime",
  };
  return Object.hasOwn(roots, n.mediaType)
    ? `${roots[n.mediaType]}/${encodeURIComponent(n.mediaId)}`
    : null;
}
