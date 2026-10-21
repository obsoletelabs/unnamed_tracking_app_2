// Browser launch links for a game. Only identifiers that match a strict shape are turned
// into a link, and only for the fixed schemes below, so stored data can never produce an
// arbitrary protocol or a script URL.
export interface LaunchTarget {
  id: "playnite" | "steam" | "gog";
  label: string;
  uri: string;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const NUMERIC_ID = /^[0-9]{1,18}$/;

export interface LaunchableGame {
  providerIds?: Record<string, string>;
  playniteGuid?: string | null;
}

export function launchTargets(game: LaunchableGame): LaunchTarget[] {
  const targets: LaunchTarget[] = [];
  const guid = game.playniteGuid?.trim() ?? "";
  if (UUID.test(guid)) {
    targets.push({
      id: "playnite",
      label: "Playnite",
      uri: `playnite://playnite/start/${guid.toLowerCase()}`,
    });
  }
  const steam = game.providerIds?.steam?.trim() ?? "";
  if (NUMERIC_ID.test(steam)) {
    targets.push({
      id: "steam",
      label: "Steam",
      uri: `steam://rungameid/${steam}`,
    });
  }
  const gog = game.providerIds?.gog?.trim() ?? "";
  if (NUMERIC_ID.test(gog)) {
    targets.push({
      id: "gog",
      label: "GOG Galaxy",
      uri: `goggalaxy://openGameView/${gog}`,
    });
  }
  return targets;
}
