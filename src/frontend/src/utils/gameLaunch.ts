// Browser launch links for a game. Only identifiers that match a strict shape are turned
// into a link, and only for the fixed schemes below, so stored data can never produce an
// arbitrary protocol or a script URL.
export type LaunchClient = "playnite" | "steam" | "gog";
export type LaunchAction = "launch" | "open" | "store";

export interface LaunchTarget {
  id: LaunchClient | "steam-store";
  label: string;
  action: LaunchAction;
  uri: string;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NUMERIC_ID = /^[0-9]{1,18}$/;

/** What a library source has recorded for this game (the game's identity "sources"). */
export interface LaunchObservation {
  source: string;
  externalId: string;
  available: boolean;
}

export interface LaunchableGame {
  providerIds?: Record<string, string>;
  playniteGuid?: string | null;
  status?: string;
  source?: string | null;
}

/**
 * Everything that can be done with this game outside the browser, most useful first.
 * A wishlisted game is not owned yet, so it only offers its store page.
 */
export function launchTargets(
  game: LaunchableGame,
  observations: LaunchObservation[] = [],
): LaunchTarget[] {
  // A Steam or GOG library import records the store's ID on its source rather than on the game,
  // and a Playnite copy may sit on another copy of the same game, so look there too.
  const fromSource = (source: string, valid: RegExp): string =>
    observations
      .filter((item) => item.available && item.source.toLowerCase() === source)
      .map((item) => item.externalId.trim())
      .find((id) => valid.test(id)) ?? "";
  const guid = (
    game.playniteGuid?.trim() || fromSource("playnite", UUID)
  ).trim();
  const steam =
    game.providerIds?.steam?.trim() || fromSource("steam", NUMERIC_ID);
  const gog = game.providerIds?.gog?.trim() || fromSource("gog", NUMERIC_ID);
  const steamStore: LaunchTarget[] = NUMERIC_ID.test(steam)
    ? [
        {
          id: "steam-store",
          label: "Steam store page",
          action: "store",
          uri: `https://store.steampowered.com/app/${steam}/`,
        },
      ]
    : [];
  if (game.status === "wishlist") return steamStore;

  const targets: LaunchTarget[] = [];
  if (UUID.test(guid)) {
    targets.push({
      id: "playnite",
      label: "Playnite",
      action: "launch",
      uri: `playnite://playnite/start/${guid.toLowerCase()}`,
    });
  }
  if (NUMERIC_ID.test(steam)) {
    targets.push({
      id: "steam",
      label: "Steam",
      action: "launch",
      uri: `steam://rungameid/${steam}`,
    });
  }
  if (NUMERIC_ID.test(gog)) {
    targets.push({
      id: "gog",
      label: "GOG Galaxy",
      action: "open",
      uri: `goggalaxy://openGameView/${gog}`,
    });
  }
  return [...targets, ...steamStore];
}

/**
 * The default action: the client the person used last, else the one this copy was added
 * from, else Playnite (it launches games from every library), then Steam, then GOG.
 * A store page is never the default while something can be launched.
 */
export function defaultTarget(
  targets: LaunchTarget[],
  game: Pick<LaunchableGame, "source">,
  remembered: string | null,
): LaunchTarget | null {
  const usable = targets.filter((target) => target.action !== "store");
  const pool = usable.length ? usable : targets;
  const source = game.source?.trim().toLowerCase() ?? "";
  return (
    pool.find((target) => target.id === remembered) ??
    pool.find((target) => target.id === source) ??
    pool[0] ??
    null
  );
}

export function targetLabel(target: LaunchTarget): string {
  if (target.action === "store") return `Open ${target.label}`;
  return `${target.action === "open" ? "Open in" : "Launch in"} ${target.label}`;
}
