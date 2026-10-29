import { mockGames } from "../data/mockGames";
import type { Game, GameStatus } from "../types/game";
import { gameAssetUrl, posterWidth, sizedAssetUrl } from "../utils/gameImages";

// The overview needs membership fields and four cover URLs per collection.
// Full game details remain on the existing game endpoint for detail views.
export type CollectionIndexGame = Pick<
  Game,
  | "id"
  | "title"
  | "status"
  | "favorite"
  | "source"
  | "tags"
  | "collections"
  | "platforms"
  | "coverImageUrl"
>;

interface BackendCollectionIndexGame {
  id: string;
  title: string;
  status: string;
  favorite: boolean;
  source: string | null;
  tags: string[];
  collections: string[];
  playtime_seconds: number;
}

export function collectionIndexFromGame(game: Game): CollectionIndexGame {
  return {
    id: game.id,
    title: game.title,
    status: game.status,
    favorite: game.favorite,
    source: game.source,
    tags: game.tags,
    collections: game.collections,
    platforms: game.platforms,
    coverImageUrl: game.coverImageUrl,
  };
}

export async function fetchCollectionIndex(): Promise<CollectionIndexGame[]> {
  if (import.meta.env.VITE_USE_MOCK_DATA === "true") return mockGames;
  const response = await fetch("/api/game/collection-index", {
    credentials: "include",
  });
  if (!response.ok) throw new Error("Failed to load game collections");
  const records = (await response.json()) as BackendCollectionIndexGame[];
  return records.map((record) => ({
    id: record.id,
    title: record.title,
    status: record.status.toLowerCase().replace(/_/g, " ") as GameStatus,
    favorite: record.favorite,
    source: record.source,
    tags: record.tags,
    collections: record.collections,
    platforms: [
      {
        platform: record.source || "PC",
        playtimeMinutes: Math.round(record.playtime_seconds / 60),
        completionPercent: null,
        lastPlayedAt: null,
      },
    ],
    coverImageUrl: sizedAssetUrl(
      gameAssetUrl(record.id, "key_art"),
      posterWidth(),
    ),
  }));
}
