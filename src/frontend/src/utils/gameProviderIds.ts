export const GAME_PROVIDER_ID_SUGGESTIONS = [
  "atlas",
  "steam",
  "gog",
  "epic",
  "tgdb",
  "itch",
  "igdb",
  "rawg",
] as const;

type StoreDefinition = {
  namespace: string;
  label: string;
  url: ((id: string) => string) | null;
};

// This is Atlas's current public store catalogue and URL policy. Keep the
// namespaces and templates aligned with unnamed_tracking_metadata/atlas/integrations.py.
const STORE_CATALOG: readonly StoreDefinition[] = [
  {
    namespace: "steam",
    label: "Steam",
    url: (id) => `https://store.steampowered.com/app/${id}`,
  },
  { namespace: "gog", label: "GOG", url: (id) => `https://www.gog.com/game/${id}` },
  {
    namespace: "epic",
    label: "Epic Games Store",
    url: (id) => `https://store.epicgames.com/p/${id}`,
  },
  {
    namespace: "tgdb",
    label: "TheGamesDB",
    url: (id) => `https://thegamesdb.net/game.php?id=${id}`,
  },
  { namespace: "itch", label: "itch.io", url: null },
  { namespace: "igdb", label: "IGDB", url: null },
  { namespace: "rawg", label: "RAWG", url: (id) => `https://rawg.io/games/${id}` },
];

const SAFE_STORE_ID = /^[A-Za-z0-9._-]{1,100}$/;

export interface GameProviderLink {
  namespace: string;
  label: string;
  id: string;
  url: string;
}

export function gameProviderLinks(
  providerIds: Record<string, string>,
): GameProviderLink[] {
  return STORE_CATALOG.flatMap(({ namespace, label, url }) => {
    const id = providerIds[namespace];
    if (!id || !url || !SAFE_STORE_ID.test(id)) return [];
    return [{ namespace, label, id, url: url(encodeURIComponent(id)) }];
  });
}
